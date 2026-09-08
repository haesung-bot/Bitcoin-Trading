"""마진 계산 검증 — 돈이 걸린 로직이라 가장 촘촘하게 본다."""

import unittest

from autocommerce.pricing.margin import (
    MarginCalculator, MarginPolicy, PricingError,
)

FEES = {"naver": {"commission": 0.0374, "link": 0.02, "ad": 0.02},
        "coupang": {"commission": 0.108, "ad": 0.02}}


def policy(**kw):
    base = dict(name="t", target_margin=0.18, vat_mode="general",
                inbound_shipping=0, outbound_shipping=3000, packaging=500,
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
            outbound_shipping=30000, guards={"max_price_multiple": 3.0}))
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
