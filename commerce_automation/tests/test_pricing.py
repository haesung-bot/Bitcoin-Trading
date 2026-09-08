"""마진 계산 검증 — 돈이 걸린 로직이라 가장 촘촘하게 본다."""

import unittest

from autocommerce.pricing.margin import (
    MarginCalculator, MarginPolicy, PricingError,
)

FEES = {"naver": {"commission": 0.0374, "link": 0.02, "ad": 0.02},
        "coupang": {"commission": 0.108, "ad": 0.02}}


def policy(**kw):
    base = dict(name="t", target_margin=0.18, vat_mode="general",
                inbound_shipping=0, shipping_cost=3000, packaging=500,
                fees=FEES, rounding={"mode": "none"})
    base.update(kw)
    return MarginPolicy(**base)


class TestSolveEvaluateRoundTrip(unittest.TestCase):
    """역산한 가격을 정방향으로 다시 계산하면 목표 마진이 나와야 한다."""

    def test_general_vat_sell_price_basis(self):
        calc = MarginCalculator(policy())
        for cost in (19000, 62000, 89000, 250000):
            price = calc.solve_price(cost, "naver")
            bd = calc.evaluate(price, cost, "naver")
            self.assertAlmostEqual(bd.margin_rate, 0.18, places=3,
                                   msg=f"cost={cost}")

    def test_no_vat_mode(self):
        calc = MarginCalculator(policy(vat_mode="none"))
        price = calc.solve_price(62000, "coupang")
        bd = calc.evaluate(price, 62000, "coupang")
        self.assertEqual(bd.vat_payable, 0)
        self.assertAlmostEqual(bd.margin_rate, 0.18, places=3)

    def test_simplified_vat_mode(self):
        calc = MarginCalculator(policy(vat_mode="simplified"))
        price = calc.solve_price(62000, "naver")
        bd = calc.evaluate(price, 62000, "naver")
        self.assertAlmostEqual(bd.margin_rate, 0.18, places=3)
        self.assertAlmostEqual(bd.vat_payable, round(price * 0.015), delta=1)

    def test_cost_basis_markup(self):
        calc = MarginCalculator(policy(margin_basis="cost", target_margin=0.25))
        cost = 50000
        price = calc.solve_price(cost, "naver")
        bd = calc.evaluate(price, cost, "naver")
        total_cost = cost + 3000 + 500
        self.assertAlmostEqual(bd.net_profit / total_cost, 0.25, places=3)

    def test_higher_fee_market_costs_more(self):
        calc = MarginCalculator(policy())
        self.assertGreater(calc.solve_price(62000, "coupang"),
                           calc.solve_price(62000, "naver"))

    def test_impossible_combination_raises(self):
        calc = MarginCalculator(policy(target_margin=0.95))
        with self.assertRaises(PricingError):
            calc.solve_price(50000, "coupang")

    def test_zero_cost_rejected(self):
        with self.assertRaises(PricingError):
            MarginCalculator(policy()).compute(0, "naver")


class TestRounding(unittest.TestCase):
    def test_charm_rounding_never_lowers_margin(self):
        calc = MarginCalculator(policy(
            rounding={"mode": "charm", "unit": 1000, "ending": 900}))
        for cost in (10000, 33333, 62000, 89000):
            raw = calc.solve_price(cost, "naver")
            rounded = calc.round_price(raw)
            self.assertGreaterEqual(rounded, raw, "반올림이 목표가 아래로 내려감")
            self.assertEqual(rounded % 1000, 900)

    def test_nearest_rounds_up_to_unit(self):
        calc = MarginCalculator(policy(rounding={"mode": "nearest", "unit": 1000}))
        self.assertEqual(calc.round_price(97181), 98000)

    def test_none_keeps_exact(self):
        calc = MarginCalculator(policy(rounding={"mode": "none"}))
        self.assertEqual(calc.round_price(97181), 97181)


class TestGuards(unittest.TestCase):
    def test_low_profit_warns(self):
        calc = MarginCalculator(policy(
            target_margin=0.01, guards={"min_net_profit": 5000}))
        bd = calc.compute(15000, "coupang")
        self.assertTrue(any("순이익" in w for w in bd.warnings), bd.warnings)

    def test_price_multiple_guard_catches_misparse(self):
        """원가가 1/10 로 잘못 파싱되면 판매가가 원가의 몇 배로 튄다."""
        calc = MarginCalculator(policy(
            shipping_cost=30000, guards={"max_price_multiple": 3.0}))
        bd = calc.compute(5000, "coupang")
        self.assertTrue(any("배" in w for w in bd.warnings), bd.warnings)

    def test_clean_case_has_no_warnings(self):
        calc = MarginCalculator(policy(guards={
            "min_price": 9900, "max_price": 900000,
            "min_net_profit": 2000, "max_price_multiple": 3.0}))
        self.assertEqual(calc.compute(62000, "naver").warnings, [])


class TestFeeResolution(unittest.TestCase):
    def test_fee_rate_sums_all_items(self):
        self.assertAlmostEqual(policy().fee_rate("naver"), 0.0774, places=4)

    def test_unknown_market_is_zero_not_crash(self):
        self.assertEqual(policy().fee_rate("11st"), 0.0)


if __name__ == "__main__":
    unittest.main()


class TestFeeVatHandling(unittest.TestCase):
    """수수료 고지 방식(VAT 포함/별도)을 섞으면 비용이 과소 계상된다."""

    def test_vat_excluded_fee_is_grossed_up(self):
        pol = policy(fees={"coupang": {
            "commission": {"rate": 0.108, "vat_included": False}}})
        self.assertAlmostEqual(pol.fee_rate("coupang"), 0.1188, places=6)

    def test_vat_included_is_the_default_for_plain_numbers(self):
        self.assertAlmostEqual(
            policy(fees={"naver": {"commission": 0.0374}}).fee_rate("naver"),
            0.0374, places=6)

    def test_explicit_vat_included_true_is_not_grossed_up(self):
        pol = policy(fees={"naver": {
            "commission": {"rate": 0.0374, "vat_included": True}}})
        self.assertAlmostEqual(pol.fee_rate("naver"), 0.0374, places=6)

    def test_mixed_forms_add_up(self):
        pol = policy(fees={"coupang": {
            "commission": {"rate": 0.108, "vat_included": False},
            "ad": 0.02,
        }})
        self.assertAlmostEqual(pol.fee_rate("coupang"), 0.1388, places=6)

    def test_missing_rate_key_raises_with_field_name(self):
        pol = policy(fees={"coupang": {"commission": {"vat_included": False}}})
        with self.assertRaises(PricingError) as ctx:
            pol.fee_rate("coupang")
        self.assertIn("coupang.commission", str(ctx.exception))

    def test_fee_detail_reports_effective_rates(self):
        pol = policy(fees={"coupang": {
            "commission": {"rate": 0.108, "vat_included": False}, "ad": 0.02}})
        detail = dict(pol.fee_detail("coupang"))
        self.assertAlmostEqual(detail["commission"], 0.1188, places=6)
        self.assertAlmostEqual(detail["ad"], 0.02, places=6)

    def test_grossed_up_fee_raises_the_price(self):
        cheap = MarginCalculator(policy(fees={"c": {"x": 0.108}}))
        real = MarginCalculator(policy(fees={"c": {
            "x": {"rate": 0.108, "vat_included": False}}}))
        self.assertGreater(real.solve_price(62000, "c"),
                           cheap.solve_price(62000, "c"))


class TestPaidShipping(unittest.TestCase):
    """유료배송은 배송비에도 수수료가 붙는다 — 총 매출 기준으로 계산해야 한다."""

    FEES = {"naver": {"commission": 0.0374, "link": 0.02}}   # 5.74%

    def paid(self, **kw):
        base = dict(shipping_mode="paid", shipping_charge=3000,
                    shipping_cost=3000, fees=self.FEES)
        base.update(kw)
        return MarginCalculator(policy(**base))

    def free(self, **kw):
        base = dict(shipping_mode="free", shipping_cost=3000, fees=self.FEES)
        base.update(kw)
        return MarginCalculator(policy(**base))

    def test_displayed_price_excludes_shipping(self):
        calc = self.paid()
        bd = calc.evaluate(calc.solve_price(62000, "naver"), 62000, "naver")
        self.assertEqual(bd.total_revenue, bd.sell_price + 3000)
        self.assertEqual(bd.shipping_charge, 3000)

    def test_target_margin_met_on_total_revenue(self):
        calc = self.paid()
        for cost in (19000, 62000, 150000):
            bd = calc.evaluate(calc.solve_price(cost, "naver"), cost, "naver")
            self.assertAlmostEqual(bd.margin_rate, 0.18, places=3, msg=f"cost={cost}")

    def test_same_total_as_free_shipping(self):
        """경제적으로 동일해야 한다 — 노출 가격만 배송비만큼 낮아진다."""
        paid = self.paid().compute(62000, "naver")
        free = self.free().compute(62000, "naver")
        self.assertEqual(paid.total_revenue, free.total_revenue)
        self.assertEqual(paid.sell_price, free.sell_price - 3000)

    def test_commission_charged_on_shipping_too(self):
        """배송비 3,000원에도 수수료가 붙는 걸 놓치면 그만큼 순손실."""
        calc = self.paid()
        bd = calc.evaluate(80000, 62000, "naver")
        self.assertEqual(bd.commission_amount, round(83000 * 0.0574))

    def test_rounding_applies_to_displayed_price_not_total(self):
        """끝자리 900은 고객에게 보이는 상품가에 걸려야 한다(총액이 아니라)."""
        calc = self.paid(rounding={"mode": "charm", "unit": 1000, "ending": 900})
        bd = calc.compute(62000, "naver")
        self.assertEqual(bd.sell_price % 1000, 900)
        self.assertEqual(bd.total_revenue, bd.sell_price + 3000)

    def test_conditional_mode_carries_threshold(self):
        calc = MarginCalculator(policy(
            shipping_mode="conditional", shipping_charge=3000,
            free_ship_over=50000, fees=self.FEES))
        self.assertEqual(calc.compute(62000, "naver").free_ship_over, 50000)


class TestShippingPolicyValidation(unittest.TestCase):
    def test_free_mode_forces_zero_charge(self):
        self.assertEqual(policy(shipping_mode="free", shipping_charge=3000)
                         .shipping_charge, 0)

    def test_paid_mode_requires_a_charge(self):
        with self.assertRaises(PricingError) as ctx:
            policy(shipping_mode="paid", shipping_charge=0)
        self.assertIn("shipping_charge", str(ctx.exception))

    def test_unknown_mode_rejected(self):
        with self.assertRaises(PricingError):
            policy(shipping_mode="halfprice")

    def test_charge_larger_than_revenue_is_caught(self):
        calc = MarginCalculator(policy(
            shipping_mode="paid", shipping_charge=500000,
            fees={"naver": {"c": 0.05}}))
        with self.assertRaises(PricingError):
            calc.solve_price(1000, "naver")
