import hashlib
import hmac
import unittest

from autocommerce.markets.base import CredentialsMissing
from autocommerce.markets.coupang import CoupangAdapter, build_authorization, signed_date
from autocommerce.markets.mapper import CategoryMapper, build_listing
from autocommerce.markets.naver import BCRYPT, NaverAdapter, client_secret_sign
from autocommerce.models import (
    MediaAsset, MediaSet, PriceBreakdown, PricedProduct, ProductOption,
    RawProduct, SeoContent,
)

FIXED_TS = 1700000000


def sample_priced():
    raw = RawProduct(
        source="fixture", source_product_id="IE3438",
        source_url="https://x.test/p?prdtNo=IE3438",
        name="아디다스 삼바", brand="아디다스", cost_price=89000,
        category_path=["신발", "운동화"],
        options=[ProductOption("사이즈", "260", 5), ProductOption("사이즈", "270", 3)],
        attributes={"색상": "화이트", "소재": "가죽", "제조사": "아디다스코리아",
                    "원산지": "베트남"},
    )
    price = PriceBreakdown(
        sell_price=129900, shipping_charge=3000, total_revenue=132900,
        cost_price=89000, inbound_shipping=0, shipping_cost=3000, packaging=500,
        commission_rate=0.0574, commission_amount=7628, vat_payable=2524,
        net_profit=25248, margin_rate=0.19, policy_name="standard",
        shipping_mode="paid",
    )
    seo = SeoContent(title="아디다스 삼바 OG 운동화", tags=["아디다스", "운동화"])
    return PricedProduct(raw=raw, price=price, seo=seo)


def sample_media():
    return MediaSet(product_uid="fixture:IE3438", assets=[
        MediaAsset(role="main", source_url="s1", local_path="/tmp/a.jpg"),
        MediaAsset(role="sub", source_url="s2", local_path="/tmp/b.jpg"),
    ])


class TestCoupangSignature(unittest.TestCase):
    PATH = "/v2/providers/seller_api/apis/api/v1/marketplace/seller-products"

    def test_matches_cea_spec(self):
        auth = build_authorization("GET", f"{self.PATH}?vendorId=A00&nextToken=1",
                                   "AK", "SK", now=FIXED_TS)
        date = signed_date(FIXED_TS)
        expected = hmac.new(
            b"SK", f"{date}GET{self.PATH}vendorId=A00&nextToken=1".encode(),
            hashlib.sha256,
        ).hexdigest()
        self.assertIn(f"signature={expected}", auth)
        self.assertIn("algorithm=HmacSHA256", auth)
        self.assertIn("access-key=AK", auth)

    def test_query_excluded_from_path_part(self):
        """서명 메시지에서 '?' 는 빠지고 쿼리 문자열만 이어 붙어야 한다."""
        with_q = build_authorization("GET", f"{self.PATH}?a=1", "AK", "SK", now=FIXED_TS)
        without = build_authorization("GET", self.PATH, "AK", "SK", now=FIXED_TS)
        self.assertNotEqual(with_q, without)

    def test_signed_date_format(self):
        date = signed_date(FIXED_TS)
        self.assertEqual(len(date), 14)   # yymmdd'T'HHMMSS'Z'
        self.assertEqual(date[6], "T")
        self.assertTrue(date.endswith("Z"))


class TestNaverSignature(unittest.TestCase):
    @unittest.skipUnless(BCRYPT, "bcrypt 미설치")
    def test_sign_is_base64_bcrypt(self):
        import base64

        import bcrypt

        salt = bcrypt.gensalt(4).decode()
        sign, ts = client_secret_sign("CID", salt, FIXED_TS)
        self.assertEqual(ts, FIXED_TS)
        decoded = base64.b64decode(sign)
        self.assertTrue(decoded.startswith(b"$2"), decoded[:4])

    @unittest.skipUnless(BCRYPT, "bcrypt 미설치")
    def test_sign_depends_on_timestamp(self):
        import bcrypt

        salt = bcrypt.gensalt(4).decode()
        a, _ = client_secret_sign("CID", salt, FIXED_TS)
        b, _ = client_secret_sign("CID", salt, FIXED_TS + 1)
        self.assertNotEqual(a, b)


class TestPreflightAndDryRun(unittest.TestCase):
    def setUp(self):
        self.mapper = CategoryMapper(
            {"운동화": {"naver": "50002380", "coupang": "63903"}}, {})
        self.priced, self.media = sample_priced(), sample_media()

    def _listing(self, adapter, **cfg_over):
        images = adapter.publish_images(self.media)
        listing, _ = build_listing(self.priced, self.media, adapter.market,
                                   self.mapper, cfg_over, images=images)
        return listing

    def test_dry_run_produces_payload_without_sending(self):
        for cls in (NaverAdapter, CoupangAdapter):
            adapter = cls({}, dry_run=True)
            result = adapter.create_listing(self._listing(adapter))
            self.assertTrue(result.ok, f"{cls.__name__}: {result.error}")
            self.assertTrue(result.dry_run)
            self.assertTrue(result.request_payload)
            self.assertIsNone(result.market_product_id)

    def test_dry_run_images_are_placeholders(self):
        adapter = NaverAdapter({}, dry_run=True)
        listing = self._listing(adapter)
        self.assertTrue(all(u.startswith("dryrun://") for u in listing.images))

    def test_long_title_blocked_by_preflight(self):
        adapter = NaverAdapter({}, dry_run=True)
        listing = self._listing(adapter)
        listing.title = "가" * 150
        result = adapter.create_listing(listing)
        self.assertFalse(result.ok)
        self.assertIn("상품명", result.error)

    def test_missing_images_blocked(self):
        adapter = CoupangAdapter({}, dry_run=True)
        listing = self._listing(adapter)
        listing.images = []
        self.assertFalse(adapter.create_listing(listing).ok)

    def test_live_without_credentials_fails_cleanly(self):
        listing = self._listing(NaverAdapter({}, dry_run=True))
        result = NaverAdapter({}, dry_run=False).create_listing(listing)
        self.assertFalse(result.ok)
        self.assertIn("자격증명", result.error)

    def test_live_upload_blocked_before_touching_files(self):
        """자격증명이 없으면 파일을 열기 전에 막혀야 한다."""
        with self.assertRaises(CredentialsMissing):
            NaverAdapter({}, dry_run=False).publish_images(self.media)


class TestPayloadShape(unittest.TestCase):
    def setUp(self):
        self.mapper = CategoryMapper(
            {"운동화": {"naver": "50002380", "coupang": "63903"}}, {})
        self.priced, self.media = sample_priced(), sample_media()

    def _build(self, adapter, cfg):
        images = adapter.publish_images(self.media)
        listing, _ = build_listing(self.priced, self.media, adapter.market,
                                   self.mapper, cfg, images=images)
        return adapter.build_payload(listing)

    def test_naver_option_combinations_mirror_source(self):
        payload = self._build(NaverAdapter({}, dry_run=True), {})
        combos = payload["originProduct"]["detailAttribute"]["optionInfo"][
            "optionCombinations"]
        self.assertEqual([c["optionName1"] for c in combos], ["260", "270"])
        self.assertEqual([c["stockQuantity"] for c in combos], [5, 3])
        self.assertEqual(payload["originProduct"]["salePrice"], 129900)

    def test_coupang_item_per_option(self):
        payload = self._build(CoupangAdapter({"vendor_id": "V1"}, dry_run=True), {})
        self.assertEqual(len(payload["items"]), 2)
        self.assertEqual(payload["displayCategoryCode"], "63903")
        self.assertTrue(
            payload["items"][0]["externalVendorSku"].startswith("fixture:IE3438"))

    def test_zero_stock_option_marked_unusable_on_naver(self):
        self.priced.raw.options[1].stock = 0
        payload = self._build(NaverAdapter({}, dry_run=True), {})
        combos = payload["originProduct"]["detailAttribute"]["optionInfo"][
            "optionCombinations"]
        self.assertTrue(combos[0]["usable"])
        self.assertFalse(combos[1]["usable"])


class TestCategoryMapper(unittest.TestCase):
    def test_leaf_wins_over_root(self):
        m = CategoryMapper({"신발": {"naver": "ROOT"}, "운동화": {"naver": "LEAF"}}, {})
        self.assertEqual(m.resolve(["신발", "운동화"], "naver")[0], "LEAF")

    def test_falls_back_with_warning(self):
        code, warn = CategoryMapper({}, {"naver": "D"}).resolve(["가방"], "naver")
        self.assertEqual(code, "D")
        self.assertIn("기본값", warn)

    def test_no_mapping_no_default_warns_loudly(self):
        code, warn = CategoryMapper({}, {}).resolve(["가방"], "coupang")
        self.assertEqual(code, "")
        self.assertIn("기본값도 없습니다", warn)


if __name__ == "__main__":
    unittest.main()


class TestShippingPolicyInPayload(unittest.TestCase):
    """마진 계산이 전제한 배송비와 마켓 등록값이 어긋나면 그대로 손실이 된다."""

    def setUp(self):
        self.mapper = CategoryMapper(
            {"운동화": {"naver": "50002380", "coupang": "63903"}}, {})
        self.media = sample_media()

    def _payload(self, adapter, **price_over):
        priced = sample_priced()
        for k, v in price_over.items():
            setattr(priced.price, k, v)
        images = adapter.publish_images(self.media)
        listing, _ = build_listing(priced, self.media, adapter.market,
                                   self.mapper, {}, images=images)
        return adapter.build_payload(listing)

    # ---------------------------------------------------------- 네이버
    def test_naver_paid_shipping(self):
        fee = self._payload(NaverAdapter({}, dry_run=True))[
            "originProduct"]["deliveryInfo"]["deliveryFee"]
        self.assertEqual(fee["deliveryFeeType"], "PAID")
        self.assertEqual(fee["baseFee"], 3000)

    def test_naver_free_shipping(self):
        fee = self._payload(NaverAdapter({}, dry_run=True),
                            shipping_mode="free", shipping_charge=0)[
            "originProduct"]["deliveryInfo"]["deliveryFee"]
        self.assertEqual(fee["deliveryFeeType"], "FREE")
        self.assertNotIn("baseFee", fee)

    def test_naver_conditional_shipping(self):
        fee = self._payload(NaverAdapter({}, dry_run=True),
                            shipping_mode="conditional", free_ship_over=50000)[
            "originProduct"]["deliveryInfo"]["deliveryFee"]
        self.assertEqual(fee["deliveryFeeType"], "CONDITIONAL_FREE")
        self.assertEqual(fee["freeConditionalAmount"], 50000)

    # ---------------------------------------------------------- 쿠팡
    def test_coupang_paid_shipping(self):
        payload = self._payload(CoupangAdapter({"vendor_id": "V"}, dry_run=True))
        self.assertEqual(payload["deliveryChargeType"], "NOT_FREE")
        self.assertEqual(payload["deliveryCharge"], 3000)

    def test_coupang_free_shipping(self):
        payload = self._payload(CoupangAdapter({"vendor_id": "V"}, dry_run=True),
                                shipping_mode="free", shipping_charge=0)
        self.assertEqual(payload["deliveryChargeType"], "FREE")
        self.assertEqual(payload["deliveryCharge"], 0)

    def test_coupang_conditional_shipping(self):
        payload = self._payload(CoupangAdapter({"vendor_id": "V"}, dry_run=True),
                                shipping_mode="conditional", free_ship_over=50000)
        self.assertEqual(payload["deliveryChargeType"], "CONDITIONAL_FREE")
        self.assertEqual(payload["freeShipOverAmount"], 50000)

    def test_item_price_is_the_displayed_price_not_total(self):
        """쿠팡 salePrice 에 배송비를 더해 보내면 이중 청구가 된다."""
        payload = self._payload(CoupangAdapter({"vendor_id": "V"}, dry_run=True))
        self.assertEqual(payload["items"][0]["salePrice"], 129900)
