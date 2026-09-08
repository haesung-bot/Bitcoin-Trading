"""마진 계산 엔진 — 목표 순마진에서 판매가를 역산한다.

기호
----
    P    상품 판매가 (VAT 포함, 마켓에 노출되는 가격)
    Sc   고객에게 청구하는 배송비 (무료배송이면 0)
    R    총 매출 = P + Sc          ← 수수료도 마진율도 전부 이 값 기준
    C    매입원가 (VAT 포함)
    Si   매입 배송비    So  실제 택배 단가(판매자가 택배사에 지불)   K  부자재
    T    총 원가 = C + Si + So + K
    r    총 매출 대비 수수료 합계율 (판매수수료 + 결제 + 매출연동 + 광고 유보)
    m    목표 마진율        N   순이익

왜 P 가 아니라 R 기준인가
------------------------
네이버·쿠팡 모두 **배송비를 포함한 결제금액 전체에 수수료를 매긴다.** 유료배송
3,000원을 따로 받으면 그 3,000원에도 수수료가 붙는다. P 기준으로 계산하면
그만큼이 통째로 새므로, 계산은 R 로 하고 마지막에 P = R − Sc 로 되돌린다.
무료배송(Sc=0)이면 R = P 라서 식이 그대로 성립한다.

1) vat_mode = general (일반과세자, 매입세액 전액 공제)
    매출세액 R/11, 매입세액 (T + R·r)/11
    납부세액 = [R − T − R·r] / 11 = G/11,   G = R(1−r) − T
    N = G − G/11 = G × 10/11

    · margin_basis = sell_price:  N = m·R
          (R(1−r) − T)·10/11 = m·R
      =>  R = T / (1 − r − 1.1·m)
    · margin_basis = cost:        N = m·T
      =>  R = T·(1 + 1.1·m) / (1 − r)

2) vat_mode = none (면세/개인, 부가세 흐름 무시)
    N = R(1−r) − T
      =>  R = T / (1 − r − m)              (sell_price 기준)
      =>  R = T(1 + m) / (1 − r)           (cost 기준)

3) vat_mode = simplified (간이과세, 소매업 부가가치율 15% 가정)
    납부세액 ≈ R × 0.15 × 0.10 = 0.015·R (매입세액공제 없음)
    N = R(1−r) − T − v·R,  v = simplified_vat_rate
      =>  R = T / (1 − r − v − m)          (sell_price 기준)
      =>  R = T(1 + m) / (1 − r − v)       (cost 기준)

분모가 0 이하가 되면(수수료+목표마진이 100% 이상) 해가 없다 — 그 조합은
애초에 성립하지 않는 장사이므로 예외로 막는다.

수수료율의 VAT 처리
-----------------
마켓마다 수수료 고지 방식이 다르다. 네이버 주문관리 수수료(3.74%)는 VAT 포함
금액이고, 쿠팡 판매수수료(10.8%)는 통상 VAT 별도로 고지된다. 이걸 섞어서 그냥
더하면 쿠팡 쪽 비용이 과소 계상된다. 그래서 설정에서 항목별로 명시할 수 있다.

    fees:
      coupang:
        commission: {rate: 0.108, vat_included: false}   # → 실효 11.88%
        ad: 0.02                                          # 숫자만 쓰면 VAT 포함
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from ..models import PriceBreakdown
from ..utils.logging import get

log = get("pricing")

SIMPLIFIED_VAT_RATE = 0.015  # 공급대가 × 15%(부가율) × 10%
SHIPPING_MODES = ("free", "paid", "conditional")


class PricingError(ValueError):
    pass


@dataclass
class MarginPolicy:
    """settings.yaml 의 pricing.policies.<name> 을 그대로 담는 값 객체."""

    name: str
    margin_basis: str = "sell_price"      # sell_price | cost
    target_margin: float = 0.18
    vat_mode: str = "general"             # general | simplified | none

    # --- 원가 ---
    inbound_shipping: int = 0             # 공홈 -> 나 (무료배송이면 0)
    shipping_cost: int = 3000             # 실제 택배 단가 (판매자가 택배사에 지불)
    packaging: int = 500

    # --- 배송비 정책 (고객에게 어떻게 청구할 것인가) ---
    shipping_mode: str = "free"           # free | paid | conditional
    shipping_charge: int = 0              # paid/conditional 일 때 고객 청구액
    free_ship_over: int = 0               # conditional 일 때 무료 기준액

    fees: dict[str, dict[str, Any]] = field(default_factory=dict)
    rounding: dict[str, Any] = field(default_factory=dict)
    guards: dict[str, Any] = field(default_factory=dict)
    simplified_vat_rate: float = SIMPLIFIED_VAT_RATE

    def __post_init__(self) -> None:
        self.rounding = self.rounding or {"mode": "charm", "unit": 1000, "ending": 900}
        if self.margin_basis not in ("sell_price", "cost"):
            raise PricingError(f"margin_basis 값이 잘못됨: {self.margin_basis}")
        if self.vat_mode not in ("general", "simplified", "none"):
            raise PricingError(f"vat_mode 값이 잘못됨: {self.vat_mode}")
        if self.shipping_mode not in SHIPPING_MODES:
            raise PricingError(
                f"shipping_mode 는 {'/'.join(SHIPPING_MODES)} 중 하나: "
                f"{self.shipping_mode}"
            )
        if not 0 <= self.target_margin < 1:
            raise PricingError(f"target_margin 은 0 이상 1 미만: {self.target_margin}")
        if self.shipping_mode == "free":
            self.shipping_charge = 0      # 무료배송이면 청구액은 항상 0
        elif self.shipping_charge <= 0:
            raise PricingError(
                f"shipping_mode={self.shipping_mode} 인데 shipping_charge 가 "
                f"{self.shipping_charge} 입니다. 고객에게 청구할 배송비를 지정하세요."
            )

    @classmethod
    def from_config(cls, name: str, cfg: dict[str, Any]) -> "MarginPolicy":
        known = {
            "margin_basis", "target_margin", "vat_mode", "inbound_shipping",
            "shipping_cost", "packaging", "shipping_mode", "shipping_charge",
            "free_ship_over", "fees", "rounding", "guards", "simplified_vat_rate",
        }
        return cls(name=name, **{k: v for k, v in (cfg or {}).items() if k in known})

    # ------------------------------------------------------------------
    @property
    def total_cost_base(self) -> int:
        """상품 원가를 뺀 고정 비용 합계."""
        return self.inbound_shipping + self.shipping_cost + self.packaging

    def fee_rate(self, market: str) -> float:
        """마켓별 수수료 항목을 전부 더한 실효 수수료율.

        항목은 숫자(VAT 포함) 또는 {rate, vat_included} 로 쓸 수 있다.
        VAT 별도 고지분은 ×1.1 해서 실제 지급액 기준으로 맞춘다.
        """
        table = self.fees.get(market)
        if table is None:
            log.warning(f"'{market}' 수수료 설정이 없어 0% 로 계산합니다. 정책: {self.name}")
            return 0.0
        return float(sum(self._one_fee(market, k, v) for k, v in table.items()))

    def _one_fee(self, market: str, key: str, value: Any) -> float:
        if isinstance(value, dict):
            try:
                rate = float(value["rate"])
            except (KeyError, TypeError, ValueError) as exc:
                raise PricingError(
                    f"수수료 항목 {market}.{key} 에 'rate' 가 필요합니다: {value}"
                ) from exc
            return rate * (1.0 if value.get("vat_included", True) else 1.1)
        return float(value)

    def fee_detail(self, market: str) -> list[tuple[str, float]]:
        """(항목명, 실효율) 목록 — doctor/price 출력에서 내역을 보여줄 때 쓴다."""
        table = self.fees.get(market) or {}
        return [(k, self._one_fee(market, k, v)) for k, v in table.items()]


class MarginCalculator:
    def __init__(self, policy: MarginPolicy) -> None:
        self.policy = policy

    # ------------------------------------------------------------ 역산
    def solve_revenue(self, cost_price: int, market: str) -> int:
        """목표 마진을 만족하는 **총 매출**(상품가 + 배송비)을 구한다."""
        p = self.policy
        total_cost = cost_price + p.total_cost_base
        r = p.fee_rate(market)
        m = p.target_margin

        if p.vat_mode == "general":
            k = 1.1                       # 순이익이 세후라 (11/10) 만큼 총이익이 더 필요
            v = 0.0
        elif p.vat_mode == "simplified":
            k = 1.0
            v = p.simplified_vat_rate
        else:
            k = 1.0
            v = 0.0

        if p.margin_basis == "sell_price":
            denom = 1 - r - v - k * m
            if denom <= 0:
                raise PricingError(
                    f"해가 없는 조합입니다: 수수료 {r:.1%} + 목표마진 {m:.1%}"
                    f"(VAT 보정 {k}배) 합계가 100% 이상입니다."
                )
            revenue = total_cost / denom
        else:  # cost 기준 마크업
            denom = 1 - r - v
            if denom <= 0:
                raise PricingError(f"수수료율 {r:.1%} 이 100% 이상입니다.")
            revenue = total_cost * (1 + k * m) / denom

        return int(math.ceil(revenue))

    def solve_price(self, cost_price: int, market: str) -> int:
        """마켓에 노출할 상품 판매가(반올림 전) = 총 매출 − 고객 청구 배송비."""
        price = self.solve_revenue(cost_price, market) - self.policy.shipping_charge
        if price <= 0:
            raise PricingError(
                f"배송비 {self.policy.shipping_charge:,}원이 목표 매출보다 큽니다 — "
                f"원가 {cost_price:,}원 조합을 확인하세요."
            )
        return price

    # ------------------------------------------------------------ 정산
    def evaluate(self, sell_price: int, cost_price: int, market: str) -> PriceBreakdown:
        """확정된 판매가로 실제 수익 구조를 다시 계산한다(정방향 검증)."""
        p = self.policy
        r = p.fee_rate(market)
        revenue = sell_price + p.shipping_charge
        commission = int(round(revenue * r))
        total_cost = cost_price + p.total_cost_base
        gross = revenue - commission - total_cost

        if p.vat_mode == "general":
            vat = int(round(gross / 11))
        elif p.vat_mode == "simplified":
            vat = int(round(revenue * p.simplified_vat_rate))
        else:
            vat = 0

        net = gross - vat
        return PriceBreakdown(
            sell_price=sell_price,
            shipping_charge=p.shipping_charge,
            total_revenue=revenue,
            cost_price=cost_price,
            inbound_shipping=p.inbound_shipping,
            shipping_cost=p.shipping_cost,
            packaging=p.packaging,
            commission_rate=r,
            commission_amount=commission,
            vat_payable=vat,
            net_profit=net,
            margin_rate=(net / revenue) if revenue else 0.0,
            policy_name=p.name,
            shipping_mode=p.shipping_mode,
            free_ship_over=p.free_ship_over,
        )

    # ------------------------------------------------------------ 반올림
    def round_price(self, price: int) -> int:
        """마켓에 노출되는 상품가만 반올림한다(배송비는 정책값 그대로)."""
        cfg = self.policy.rounding
        mode = cfg.get("mode", "charm")
        unit = int(cfg.get("unit", 1000))
        if mode == "none" or unit <= 0:
            return int(price)
        if mode == "nearest":
            return int(math.ceil(price / unit) * unit)
        # charm: 끝자리를 ending(예: 900)으로 맞추되 목표가 아래로 내려가지 않게 올림
        ending = int(cfg.get("ending", 900))
        steps = max(math.ceil((price - ending) / unit), 0)
        return steps * unit + ending

    # ------------------------------------------------------------ 최종
    def compute(self, cost_price: int, market: str) -> PriceBreakdown:
        """원가 -> 최종 판매가 + 손익 내역 + 가드 경고."""
        if cost_price <= 0:
            raise PricingError(f"매입원가가 유효하지 않습니다: {cost_price}")

        price = self.round_price(self.solve_price(cost_price, market))
        bd = self.evaluate(price, cost_price, market)
        bd.warnings = self._check_guards(bd, cost_price)
        return bd

    def _check_guards(self, bd: PriceBreakdown, cost_price: int) -> list[str]:
        g = self.policy.guards
        out: list[str] = []
        if (mn := g.get("min_price")) and bd.sell_price < int(mn):
            out.append(f"판매가 {bd.sell_price:,}원이 최소가 {int(mn):,}원 미만")
        if (mx := g.get("max_price")) and bd.sell_price > int(mx):
            out.append(f"판매가 {bd.sell_price:,}원이 최대가 {int(mx):,}원 초과")
        if (mp := g.get("min_net_profit")) and bd.net_profit < int(mp):
            out.append(
                f"순이익 {bd.net_profit:,}원이 하한 {int(mp):,}원 미만 — 등록 보류 권장"
            )
        if (mult := g.get("max_price_multiple")) and cost_price:
            if bd.total_revenue > cost_price * float(mult):
                out.append(
                    f"총 매출이 원가의 {bd.total_revenue / cost_price:.1f}배 — "
                    f"허용 배수 {float(mult)}배 초과(가격 오파싱 의심)"
                )
        if bd.net_profit <= 0:
            out.append("순이익이 0 이하입니다 — 등록하면 손실")
        return out


def load_policies(settings) -> dict[str, MarginPolicy]:
    """Settings -> {정책명: MarginPolicy}"""
    raw = settings.section("pricing").get("policies", {}) or {}
    return {name: MarginPolicy.from_config(name, cfg) for name, cfg in raw.items()}
