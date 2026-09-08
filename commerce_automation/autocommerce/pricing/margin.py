"""마진 계산 엔진 — 목표 순마진에서 판매가를 역산한다.

기호
----
    P   판매가 (VAT 포함, 고객 결제액)
    C   매입원가 (VAT 포함)
    Si  매입 배송비        So  고객 발송 배송비        K  부자재/포장비
    T   총 원가 = C + Si + So + K
    r   판매가 대비 수수료 합계율 (판매수수료 + 결제 + 매출연동 + 광고 유보)
    m   목표 마진율        N   순이익

1) vat_mode = general (일반과세자, 매입세액 전액 공제)
    매출세액 P/11, 매입세액 (T + P·r)/11
    납부세액 = [P − T − P·r] / 11 = G/11,   G = P(1−r) − T
    N = G − G/11 = G × 10/11

    · margin_basis = sell_price:  N = m·P
          (P(1−r) − T)·10/11 = m·P
      =>  P = T / (1 − r − 1.1·m)
    · margin_basis = cost:        N = m·T
      =>  P = T·(1 + 1.1·m) / (1 − r)

2) vat_mode = none (면세/개인, 부가세 흐름 무시)
    N = P(1−r) − T
      =>  P = T / (1 − r − m)              (sell_price 기준)
      =>  P = T(1 + m) / (1 − r)           (cost 기준)

3) vat_mode = simplified (간이과세, 소매업 부가가치율 15% 가정)
    납부세액 ≈ P × 0.15 × 0.10 = 0.015·P (매입세액공제 없음)
    N = P(1−r) − T − v·P,  v = simplified_vat_rate
      =>  P = T / (1 − r − v − m)          (sell_price 기준)
      =>  P = T(1 + m) / (1 − r − v)       (cost 기준)

분모가 0 이하가 되면(수수료+목표마진이 100% 이상) 해가 없다 — 그 조합은
애초에 성립하지 않는 장사이므로 예외로 막는다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from ..models import PriceBreakdown
from ..utils.logging import get

log = get("pricing")

SIMPLIFIED_VAT_RATE = 0.015  # 공급대가 × 15%(부가율) × 10%


class PricingError(ValueError):
    pass


@dataclass
class MarginPolicy:
    """settings.yaml 의 pricing.policies.<name> 을 그대로 담는 값 객체."""

    name: str
    margin_basis: str = "sell_price"      # sell_price | cost
    target_margin: float = 0.18
    vat_mode: str = "general"             # general | simplified | none
    inbound_shipping: int = 0
    outbound_shipping: int = 3000
    packaging: int = 500
    fees: dict[str, dict[str, float]] = None            # market -> {항목: 율}
    rounding: dict[str, Any] = None
    guards: dict[str, Any] = None
    simplified_vat_rate: float = SIMPLIFIED_VAT_RATE

    def __post_init__(self) -> None:
        self.fees = self.fees or {}
        self.rounding = self.rounding or {"mode": "charm", "unit": 1000, "ending": 900}
        self.guards = self.guards or {}
        if self.margin_basis not in ("sell_price", "cost"):
            raise PricingError(f"margin_basis 값이 잘못됨: {self.margin_basis}")
        if self.vat_mode not in ("general", "simplified", "none"):
            raise PricingError(f"vat_mode 값이 잘못됨: {self.vat_mode}")
        if not 0 <= self.target_margin < 1:
            raise PricingError(f"target_margin 은 0 이상 1 미만: {self.target_margin}")

    @classmethod
    def from_config(cls, name: str, cfg: dict[str, Any]) -> "MarginPolicy":
        known = {
            "margin_basis", "target_margin", "vat_mode", "inbound_shipping",
            "outbound_shipping", "packaging", "fees", "rounding", "guards",
            "simplified_vat_rate",
        }
        return cls(name=name, **{k: v for k, v in (cfg or {}).items() if k in known})

    def fee_rate(self, market: str) -> float:
        """마켓별 수수료 항목을 전부 더한 실효 수수료율."""
        table = self.fees.get(market)
        if table is None:
            log.warning(f"'{market}' 수수료 설정이 없어 0% 로 계산합니다. 정책: {self.name}")
            return 0.0
        return float(sum(float(v) for v in table.values()))


class MarginCalculator:
    def __init__(self, policy: MarginPolicy) -> None:
        self.policy = policy

    # ------------------------------------------------------------ 역산
    def solve_price(self, cost_price: int, market: str) -> int:
        """목표 마진을 만족하는 판매가(반올림 전)를 구한다."""
        p = self.policy
        total_cost = cost_price + p.inbound_shipping + p.outbound_shipping + p.packaging
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
            price = total_cost / denom
        else:  # cost 기준 마크업
            denom = 1 - r - v
            if denom <= 0:
                raise PricingError(f"수수료율 {r:.1%} 이 100% 이상입니다.")
            price = total_cost * (1 + k * m) / denom

        return int(math.ceil(price))

    # ------------------------------------------------------------ 정산
    def evaluate(self, sell_price: int, cost_price: int, market: str) -> PriceBreakdown:
        """확정된 판매가로 실제 수익 구조를 다시 계산한다(정방향 검증)."""
        p = self.policy
        r = p.fee_rate(market)
        commission = int(round(sell_price * r))
        total_cost = cost_price + p.inbound_shipping + p.outbound_shipping + p.packaging
        gross = sell_price - commission - total_cost

        if p.vat_mode == "general":
            vat = int(round(gross / 11)) if gross > 0 else int(round(gross / 11))
        elif p.vat_mode == "simplified":
            vat = int(round(sell_price * p.simplified_vat_rate))
        else:
            vat = 0

        net = gross - vat
        return PriceBreakdown(
            sell_price=sell_price,
            cost_price=cost_price,
            inbound_shipping=p.inbound_shipping,
            outbound_shipping=p.outbound_shipping,
            packaging=p.packaging,
            commission_rate=r,
            commission_amount=commission,
            vat_payable=vat,
            net_profit=net,
            margin_rate=(net / sell_price) if sell_price else 0.0,
            policy_name=p.name,
        )

    # ------------------------------------------------------------ 반올림
    def round_price(self, price: int) -> int:
        cfg = self.policy.rounding
        mode = cfg.get("mode", "charm")
        unit = int(cfg.get("unit", 1000))
        if mode == "none" or unit <= 0:
            return int(price)
        if mode == "nearest":
            return int(math.ceil(price / unit) * unit)
        # charm: 끝자리를 ending(예: 900)으로 맞추되 목표가 아래로 내려가지 않게 올림
        ending = int(cfg.get("ending", 900))
        steps = math.ceil((price - ending) / unit)
        steps = max(steps, 0)
        return steps * unit + ending

    # ------------------------------------------------------------ 최종
    def compute(self, cost_price: int, market: str) -> PriceBreakdown:
        """원가 -> 최종 판매가 + 손익 내역 + 가드 경고."""
        if cost_price <= 0:
            raise PricingError(f"매입원가가 유효하지 않습니다: {cost_price}")

        raw_price = self.solve_price(cost_price, market)
        price = self.round_price(raw_price)
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
            if bd.sell_price > cost_price * float(mult):
                out.append(
                    f"판매가가 원가의 {bd.sell_price / cost_price:.1f}배 — "
                    f"허용 배수 {float(mult)}배 초과(가격 오파싱 의심)"
                )
        if bd.net_profit <= 0:
            out.append("순이익이 0 이하입니다 — 등록하면 손실")
        return out


def load_policies(settings) -> dict[str, MarginPolicy]:
    """Settings -> {정책명: MarginPolicy}"""
    raw = settings.section("pricing").get("policies", {}) or {}
    return {name: MarginPolicy.from_config(name, cfg) for name, cfg in raw.items()}
