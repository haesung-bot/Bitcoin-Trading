"""1-Click 확인 구매 — '결제는 절대 자동화하지 않는다'는 원칙을 코드로 못 박는다."""

import tempfile
import unittest
from pathlib import Path

from autocommerce.models import (
    PriceBreakdown, PricedProduct, PurchaseState, PurchaseTask, RawProduct, SeoContent,
)
from autocommerce.orders.one_click import OneClickConfig, OneClickPurchase, row_to_task
from autocommerce.storage import Store


def task(**kw):
    base = dict(
        task_id="naver:12345", market="naver", market_order_id="12345",
        product_uid="fixture:A1", option_value="270", quantity=1,
        buyer_paid=99000, expected_cost=62000,
        receiver={"name": "홍길동", "phone": "010-0000-0000",
                  "zipcode": "06236", "address": "서울시 강남구",
                  "address_detail": "101동 101호"},
        source_url="https://shop.test/p?prdtNo=A1",
    )
    base.update(kw)
    return PurchaseTask(**base)


class OneClickCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.dir.name) / "t.db")
        raw = RawProduct(source="fixture", source_product_id="A1",
                         source_url="https://shop.test/p?prdtNo=A1", name="상품",
                         brand="B", cost_price=62000, image_urls=["i"])
        self.store.save_raw(raw)
        self.store.save_priced(PricedProduct(
            raw=raw,
            price=PriceBreakdown(99000, 62000, 0, 3000, 500, 0.0774, 7663, 2348,
                                 23489, 0.24, "standard"),
            seo=SeoContent(title="상품")))

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def oc(self, **cfg):
        return OneClickPurchase(self.store, OneClickConfig(**cfg))


class TestPrepare(OneClickCase):
    def test_fills_expected_cost_from_store(self):
        t = self.oc().prepare(task(expected_cost=0))
        self.assertEqual(t.expected_cost, 62000)

    def test_deeplink_carries_option(self):
        t = self.oc().prepare(task())
        self.assertIn("size=270", t.deeplink)
        self.assertTrue(t.deeplink.startswith("https://shop.test/"))

    def test_starts_pending_always(self):
        t = self.oc(auto_approve=True).prepare(task())
        self.assertEqual(t.state, PurchaseState.PENDING.value)

    def test_cost_drift_recorded_and_noted(self):
        t = self.oc(cost_drift_block=3000).prepare(task(), current_cost=68000)
        self.assertEqual(t.cost_drift, 6000)
        self.assertIn("올랐습니다", t.note)


class TestAutoApproveGuards(OneClickCase):
    def test_default_never_auto_approves(self):
        ok, why = self.oc().can_auto_approve(self.oc().prepare(task()))
        self.assertFalse(ok)
        self.assertIn("사람 확인", why)

    def test_cost_spike_blocks_even_when_enabled(self):
        oc = self.oc(auto_approve=True, cost_drift_block=3000)
        t = oc.prepare(task(), current_cost=70000)
        ok, why = oc.can_auto_approve(t)
        self.assertFalse(ok)
        self.assertIn("차단 임계", why)

    def test_negative_margin_blocked(self):
        oc = self.oc(auto_approve=True, cost_drift_block=100000)
        t = oc.prepare(task(buyer_paid=50000))
        ok, why = oc.can_auto_approve(t)
        self.assertFalse(ok)
        self.assertIn("역마진", why)

    def test_healthy_order_passes_when_explicitly_enabled(self):
        oc = self.oc(auto_approve=True)
        ok, _ = oc.can_auto_approve(oc.prepare(task()))
        self.assertTrue(ok)


class TestApprovalFlow(OneClickCase):
    def test_approve_without_order_no_is_approved_not_ordered(self):
        self.store.upsert_task(self.oc().prepare(task()))
        self.oc().approve("naver:12345")
        self.assertEqual(self.store.get_task("naver:12345")["state"],
                         PurchaseState.APPROVED.value)

    def test_approve_with_order_no_marks_ordered(self):
        self.store.upsert_task(self.oc().prepare(task()))
        self.oc().approve("naver:12345", order_no="ORD-99")
        row = self.store.get_task("naver:12345")
        self.assertEqual(row["state"], PurchaseState.ORDERED.value)
        self.assertIn("ORD-99", row["note"])

    def test_reject_records_reason(self):
        self.store.upsert_task(self.oc().prepare(task()))
        self.oc().reject("naver:12345", "공홈 품절")
        row = self.store.get_task("naver:12345")
        self.assertEqual(row["state"], PurchaseState.REJECTED.value)
        self.assertEqual(row["note"], "공홈 품절")

    def test_round_trip_through_storage(self):
        self.store.upsert_task(self.oc().prepare(task()))
        restored = row_to_task(self.store.get_task("naver:12345"))
        self.assertEqual(restored.option_value, "270")
        self.assertEqual(restored.receiver["name"], "홍길동")


class TestCard(OneClickCase):
    def test_card_shows_money_and_address(self):
        card = self.oc().card(self.oc().prepare(task()))
        for expected in ("99,000원", "62,000원", "홍길동", "서울시 강남구",
                         "결제하지 않습니다"):
            self.assertIn(expected, card)

    def test_card_flags_cost_drift(self):
        oc = self.oc()
        card = oc.card(oc.prepare(task(), current_cost=68000))
        self.assertIn("매입가 변동", card)
        self.assertIn("+6,000", card)


if __name__ == "__main__":
    unittest.main()
