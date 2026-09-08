"""1-Click 확인 구매.

왜 결제까지 자동화하지 않는가
--------------------------
공식몰 결제는 PG 3D Secure, 간편결제 앱 인증, CAPTCHA, 1인 구매 수량 제한,
그리고 재고/가격 변동이 실시간으로 얽힌다. 이걸 스크립트로 뚫으면
(a) 전자금융거래법·사이트 약관 위반 소지, (b) 카드사 이상거래 탐지로 카드 정지,
(c) 가격이 오른 줄 모르고 역마진 주문이 대량 발생한다.

그래서 시스템의 책임 범위를 이렇게 자른다.

    [자동] 주문 수집 -> 원가 재확인 -> 배송지 정규화 -> 구매 링크/입력값 준비
    ------------------------------ 사람 1회 확인 ------------------------------
    [수동] 결제 승인 (사용자가 열린 페이지에서 클릭)
    [자동] 주문번호/송장 기록

`prepare()` 가 확인 카드를 만들고, `approve()` 는 사람이 승인 버튼을 눌렀을 때만
호출된다. 원가가 `cost_drift_block` 이상 올랐으면 자동 승인 후보에서도 빠진다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from ..models import PurchaseState, PurchaseTask
from ..storage import Store
from ..utils.logging import get

log = get("orders.oneclick")


@dataclass
class OneClickConfig:
    auto_approve: bool = False
    cost_drift_block: int = 3000

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> "OneClickConfig":
        return cls(
            auto_approve=bool((cfg or {}).get("auto_approve", False)),
            cost_drift_block=int((cfg or {}).get("cost_drift_block", 3000)),
        )


class OneClickPurchase:
    def __init__(self, store: Store, cfg: OneClickConfig) -> None:
        self.store = store
        self.cfg = cfg

    # ------------------------------------------------------------------
    def prepare(self, task: PurchaseTask, *, current_cost: int | None = None
                ) -> PurchaseTask:
        """주문 -> 확인 대기 작업. 원가 변동을 계산해 붙인다."""
        expected = task.expected_cost or self.store.expected_cost(task.product_uid)
        task.expected_cost = expected
        if current_cost is not None and expected:
            task.cost_drift = current_cost - expected

        priced = self.store.get_priced(task.product_uid) or {}
        task.source_url = task.source_url or priced.get("raw", {}).get("source_url", "")
        task.deeplink = self._deeplink(task)
        task.state = PurchaseState.PENDING.value

        if task.cost_drift >= self.cfg.cost_drift_block:
            task.note = (
                f"매입가가 {task.cost_drift:,}원 올랐습니다 "
                f"(기준 {expected:,}원). 판매가 조정 여부를 먼저 확인하세요."
            )
        return task

    def _deeplink(self, task: PurchaseTask) -> str:
        """공홈 상품 페이지 + 옵션 프리필. 실제 파라미터명은 사이트마다 다르므로
        settings 로 뺄 수 있게 두되, 기본은 URL 프래그먼트로 옵션을 실어 보낸다."""
        if not task.source_url:
            return ""
        sep = "&" if "?" in task.source_url else "?"
        return f"{task.source_url}{sep}size={quote(task.option_value)}#autocommerce"

    # ------------------------------------------------------------------
    def card(self, task: PurchaseTask) -> str:
        """터미널에 띄우는 1-Click 확인 카드."""
        margin = task.buyer_paid - (task.expected_cost + task.cost_drift)
        drift = (
            f"│ ⚠ 매입가 변동  {task.cost_drift:+,}원\n" if task.cost_drift else ""
        )
        r = task.receiver
        return (
            f"\n┌─ 확인 구매 #{task.task_id}\n"
            f"│ 마켓/주문     {task.market} / {task.market_order_id}\n"
            f"│ 상품          {task.product_uid}\n"
            f"│ 옵션 / 수량   {task.option_value} / {task.quantity}개\n"
            f"│ 고객 결제액   {task.buyer_paid:,}원\n"
            f"│ 예상 매입가   {task.expected_cost:,}원\n"
            f"{drift}"
            f"│ 예상 차익     {margin:,}원\n"
            f"│ 배송지        {r.get('name')} / {r.get('phone')}\n"
            f"│               ({r.get('zipcode')}) {r.get('address')} "
            f"{r.get('address_detail') or ''}\n"
            f"│ 구매 링크     {task.deeplink or '(원본 URL 없음)'}\n"
            f"└─ 결제는 위 링크에서 직접 진행하세요. 시스템은 결제하지 않습니다.\n"
        )

    # ------------------------------------------------------------------
    def can_auto_approve(self, task: PurchaseTask) -> tuple[bool, str]:
        if not self.cfg.auto_approve:
            return False, "auto_approve=false (기본값) — 사람 확인 필요"
        if task.cost_drift >= self.cfg.cost_drift_block:
            return False, f"매입가 {task.cost_drift:,}원 상승 — 차단 임계 초과"
        if task.buyer_paid <= task.expected_cost + task.cost_drift:
            return False, "역마진 주문 — 자동 승인 차단"
        return True, "임계 조건 통과"

    def approve(self, task_id: str, *, order_no: str = "", note: str = "") -> None:
        """사람이 결제를 마친 뒤 주문번호를 기록한다."""
        state = PurchaseState.ORDERED.value if order_no else PurchaseState.APPROVED.value
        message = f"공홈주문번호={order_no} {note}".strip()
        self.store.set_task_state(task_id, state, message)
        log.info(f"확인 구매 승인: {task_id} -> {state}")

    def reject(self, task_id: str, reason: str) -> None:
        self.store.set_task_state(task_id, PurchaseState.REJECTED.value, reason)
        log.info(f"확인 구매 거절: {task_id} ({reason})")


def row_to_task(row) -> PurchaseTask:
    """SQLite Row -> PurchaseTask"""
    import json

    return PurchaseTask(
        task_id=row["task_id"], market=row["market"], market_order_id=row["order_id"],
        product_uid=row["uid"] or "", option_value=row["option_value"] or "",
        quantity=row["quantity"] or 1, buyer_paid=row["buyer_paid"] or 0,
        expected_cost=row["expected_cost"] or 0, cost_drift=row["cost_drift"] or 0,
        receiver=json.loads(row["receiver_json"] or "{}"),
        source_url="", deeplink=row["deeplink"], state=row["state"],
        note=row["note"] or "", created_at=row["created_at"],
    )
