import tempfile
import unittest
from pathlib import Path

from autocommerce.models import (
    ListingResult, PriceBreakdown, PricedProduct, ProductOption, RawProduct,
    SeoContent, Stage,
)
from autocommerce.storage import Store


def raw(cost=50000, pid="A1"):
    return RawProduct(source="fixture", source_product_id=pid,
                      source_url=f"https://x.test/{pid}", name="상품", brand="B",
                      cost_price=cost, image_urls=["i"],
                      options=[ProductOption("사이즈", "260", 3)])


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.dir.name) / "t.db")

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()


class TestIdempotency(StoreCase):
    def test_new_product_is_processed(self):
        ok, why = self.store.should_process(raw())
        self.assertTrue(ok)
        self.assertEqual(why, "신규")

    def test_unchanged_and_listed_is_skipped(self):
        p = raw()
        self.store.save_raw(p)
        self.store.save_listing(ListingResult(
            product_uid=p.uid, market="naver", ok=True,
            market_product_id="123", dry_run=False))
        ok, why = self.store.should_process(p)
        self.assertFalse(ok)
        self.assertIn("이미 등록됨", why)

    def test_price_change_reprocesses(self):
        p = raw(cost=50000)
        self.store.save_raw(p)
        self.store.save_listing(ListingResult(
            product_uid=p.uid, market="naver", ok=True, dry_run=False))
        ok, why = self.store.should_process(raw(cost=55000))
        self.assertTrue(ok)
        self.assertIn("변경 감지", why)

    def test_stock_change_reprocesses(self):
        p = raw()
        self.store.save_raw(p)
        self.store.save_listing(ListingResult(
            product_uid=p.uid, market="naver", ok=True, dry_run=False))
        changed = raw()
        changed.options[0].stock = 99
        self.assertTrue(self.store.should_process(changed)[0])

    def test_failed_is_retried(self):
        p = raw()
        self.store.save_raw(p)
        self.store.mark_failed(p.uid, "일시적 오류")
        ok, why = self.store.should_process(p)
        self.assertTrue(ok)
        self.assertIn("재시도", why)

    def test_force_overrides_everything(self):
        p = raw()
        self.store.save_raw(p)
        self.store.save_listing(ListingResult(
            product_uid=p.uid, market="naver", ok=True, dry_run=False))
        self.assertTrue(self.store.should_process(p, force=True)[0])

    def test_dry_run_listing_does_not_mark_listed(self):
        """dry-run 은 '등록됨'이 아니다 — 다음 실행에서 다시 처리되어야 한다."""
        p = raw()
        self.store.save_raw(p)
        self.store.save_listing(ListingResult(
            product_uid=p.uid, market="naver", ok=True, dry_run=True))
        self.assertNotIn(Stage.LISTED.value, self.store.counts_by_stage())
        self.assertTrue(self.store.should_process(p)[0])


class TestStageProgression(StoreCase):
    def test_stage_never_goes_backwards(self):
        """파이프라인은 이미지(3단계)를 마켓별 가격(2단계)보다 먼저 돌린다."""
        p = raw()
        self.store.save_raw(p)
        self.store.save_media(p.uid, _dummy_media(p.uid))
        priced = PricedProduct(
            raw=p,
            price=PriceBreakdown(70000, 50000, 0, 3000, 500, 0.08, 5600, 1000,
                                 12000, 0.17, "standard"),
            seo=SeoContent(title="t"),
        )
        self.store.save_priced(priced)
        self.assertEqual(self.store.counts_by_stage(), {Stage.IMAGED.value: 1})

    def test_failed_always_recorded(self):
        p = raw()
        self.store.save_raw(p)
        self.store.save_media(p.uid, _dummy_media(p.uid))
        self.store.mark_failed(p.uid, "boom")
        self.assertEqual(self.store.counts_by_stage(), {Stage.FAILED.value: 1})


def _dummy_media(uid):
    from autocommerce.models import MediaSet
    return MediaSet(product_uid=uid)


class TestPurchaseTasks(StoreCase):
    def _task(self, tid="t1"):
        from autocommerce.models import PurchaseTask
        return PurchaseTask(
            task_id=tid, market="naver", market_order_id="O1",
            product_uid="fixture:A1", option_value="260", quantity=1,
            buyer_paid=90000, expected_cost=50000, receiver={"name": "홍길동"},
            source_url="https://x.test/A1",
        )

    def test_upsert_is_idempotent(self):
        self.assertTrue(self.store.upsert_task(self._task()))
        self.assertFalse(self.store.upsert_task(self._task()))
        self.assertEqual(len(self.store.pending_tasks()), 1)

    def test_state_transition_removes_from_pending(self):
        self.store.upsert_task(self._task())
        self.store.set_task_state("t1", "approved", "확인 완료")
        self.assertEqual(self.store.pending_tasks(), [])
        self.assertEqual(self.store.get_task("t1")["state"], "approved")

    def test_resync_does_not_reset_approved_task(self):
        """주문을 다시 동기화해도 이미 승인한 건을 pending 으로 되돌리면 안 된다."""
        self.store.upsert_task(self._task())
        self.store.set_task_state("t1", "ordered", "주문번호=X")
        self.store.upsert_task(self._task())
        self.assertEqual(self.store.get_task("t1")["state"], "ordered")


class TestAuditTrail(StoreCase):
    def test_listing_payload_is_persisted(self):
        p = raw()
        self.store.save_raw(p)
        self.store.save_listing(ListingResult(
            product_uid=p.uid, market="coupang", ok=False,
            error="카테고리 없음", request_payload={"a": 1}, dry_run=True))
        row = self.store.recent_listings(1)[0]
        self.assertEqual(row["error"], "카테고리 없음")
        self.assertIn('"a": 1', row["request_json"])

    def test_expected_cost_read_back(self):
        p = raw(cost=61000)
        self.store.save_raw(p)
        self.store.save_priced(PricedProduct(
            raw=p,
            price=PriceBreakdown(90000, 61000, 0, 3000, 500, 0.08, 7200, 1600,
                                 16700, 0.18, "standard"),
            seo=SeoContent(title="t")))
        self.assertEqual(self.store.expected_cost(p.uid), 61000)


if __name__ == "__main__":
    unittest.main()
