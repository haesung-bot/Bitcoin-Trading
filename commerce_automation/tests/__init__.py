"""테스트 공통 설정 — 로그 소음을 줄인다."""

import logging

logging.getLogger("autocommerce").setLevel(logging.CRITICAL)


def breakdown(sell_price: int, cost_price: int, **over):
    """PriceBreakdown 생성 헬퍼.

    필드가 늘어날 때마다 테스트의 위치 인자가 통째로 깨지는 걸 막는다.
    검증하려는 값만 키워드로 덮어쓰면 된다.
    """
    from autocommerce.models import PriceBreakdown

    args = dict(
        sell_price=sell_price,
        shipping_charge=0,
        total_revenue=sell_price,
        cost_price=cost_price,
        inbound_shipping=0,
        shipping_cost=3000,
        packaging=500,
        commission_rate=0.0574,
        commission_amount=int(sell_price * 0.0574),
        vat_payable=0,
        net_profit=0,
        margin_rate=0.0,
        policy_name="standard",
    )
    args.update(over)
    args.setdefault("total_revenue", args["sell_price"] + args["shipping_charge"])
    return PriceBreakdown(**args)
