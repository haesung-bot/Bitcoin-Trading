"""실행 중에도 바꿀 수 있는 매매 설정값 저장소.

기존 봇은 '자동매매 시작'을 누른 시점에 GUI 위젯에서 설정값을 한 번만 읽고,
그 값을 매매 루프가 끝날 때까지 그대로 썼다. 그래서 레버리지나 주문 금액을
바꾸려면 봇을 껐다 켜야 했다.

RuntimeConfig는 그 설정값들을 스레드 안전한 단일 저장소로 옮긴 것이다.
매매 루프는 매 주기마다 snapshot()으로 최신 값을 읽어가므로, 제어 계층에서
값을 바꾸면 다음 주기부터 바로 반영된다.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, NamedTuple


class ParamError(ValueError):
    """설정값이 허용 범위를 벗어났을 때 발생."""


class ParamSpec(NamedTuple):
    """설정 항목 하나의 명세 — 어떻게 변환하고 무엇을 허용하는지."""

    coerce: Callable[[Any], Any]
    describe: str


def _leverage(value: Any) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ParamError("레버리지는 정수여야 합니다.") from None
    if not 1 <= result <= 125:
        raise ParamError("레버리지는 1 이상 125 이하여야 합니다.")
    return result


def _amount_mode(value: Any) -> str:
    result = str(value).strip().lower()
    if result not in ("fixed", "balance_pct"):
        raise ParamError("주문 금액 방식은 'fixed' 또는 'balance_pct'여야 합니다.")
    return result


def _fixed_amount(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ParamError("고정 주문 금액은 숫자여야 합니다.") from None
    if result <= 0:
        raise ParamError("고정 주문 금액은 0보다 커야 합니다.")
    return result


def _balance_pct(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ParamError("잔고 사용 비율은 숫자여야 합니다.") from None
    if not 0 < result <= 100:
        raise ParamError("잔고 사용 비율은 0보다 크고 100 이하여야 합니다.")
    return result


def _poll_sec(value: Any) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ParamError("폴링 주기는 정수(초)여야 합니다.") from None
    if not 3 <= result <= 300:
        raise ParamError("폴링 주기는 3초 이상 300초 이하여야 합니다.")
    return result


def _entries_enabled(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    result = str(value).strip().lower()
    if result in ("true", "1", "yes", "on", "y"):
        return True
    if result in ("false", "0", "no", "off", "n"):
        return False
    raise ParamError("신규 진입 허용 여부는 true/false여야 합니다.")


PARAM_SPECS: dict[str, ParamSpec] = {
    "leverage": ParamSpec(_leverage, "레버리지 배수 (1~125)"),
    "amount_mode": ParamSpec(_amount_mode, "주문 금액 방식 (fixed | balance_pct)"),
    "fixed_amount_usdt": ParamSpec(_fixed_amount, "1회 고정 주문 금액 (USDT, 0 초과)"),
    "balance_pct": ParamSpec(_balance_pct, "잔고 사용 비율 (%, 0 초과 100 이하)"),
    "poll_sec": ParamSpec(_poll_sec, "매매 판단 주기 (초, 3~300)"),
    "entries_enabled": ParamSpec(_entries_enabled, "신규 진입 허용 여부 (true | false)"),
}

DEFAULTS: dict[str, Any] = {
    "leverage": 1,
    "amount_mode": "fixed",
    "fixed_amount_usdt": None,
    "balance_pct": None,
    "poll_sec": 10,
    "entries_enabled": True,
}


class RuntimeConfig:
    """스레드 안전한 설정 저장소."""

    def __init__(self, **overrides: Any) -> None:
        self._lock = threading.RLock()
        self._values = dict(DEFAULTS)
        if overrides:
            self.update(overrides)

    def snapshot(self) -> dict[str, Any]:
        """현재 설정 전체의 사본. 매매 루프가 매 주기마다 이걸 읽는다."""
        with self._lock:
            return dict(self._values)

    def get(self, name: str) -> Any:
        with self._lock:
            if name not in self._values:
                raise ParamError(f"알 수 없는 설정 항목입니다: {name}")
            return self._values[name]

    def set(self, name: str, value: Any) -> Any:
        """설정값 하나를 검증 후 변경하고, 변환된 값을 돌려준다."""
        spec = PARAM_SPECS.get(name)
        if spec is None:
            known = ", ".join(sorted(PARAM_SPECS))
            raise ParamError(f"알 수 없는 설정 항목입니다: {name} (가능한 항목: {known})")
        coerced = spec.coerce(value)
        with self._lock:
            self._values[name] = coerced
        return coerced

    def update(self, values: dict[str, Any]) -> dict[str, Any]:
        """여러 값을 한 번에 변경한다.

        하나라도 검증에 실패하면 아무것도 바꾸지 않는다 (all-or-nothing).
        일부만 반영돼서 설정이 뒤섞이는 상황을 막기 위함이다.
        """
        coerced: dict[str, Any] = {}
        for name, value in values.items():
            spec = PARAM_SPECS.get(name)
            if spec is None:
                known = ", ".join(sorted(PARAM_SPECS))
                raise ParamError(f"알 수 없는 설정 항목입니다: {name} (가능한 항목: {known})")
            coerced[name] = spec.coerce(value)

        with self._lock:
            self._values.update(coerced)
        return coerced

    def validate_for_start(self) -> None:
        """매매를 시작해도 되는 설정인지 확인한다.

        주문 금액 방식에 맞는 값이 채워져 있지 않으면 시작 시점에 막는다.
        """
        with self._lock:
            values = dict(self._values)

        if values["amount_mode"] == "fixed":
            if values["fixed_amount_usdt"] is None:
                raise ParamError("고정 금액 모드입니다. fixed_amount_usdt를 먼저 설정해주세요.")
        elif values["balance_pct"] is None:
            raise ParamError("잔고 비율 모드입니다. balance_pct를 먼저 설정해주세요.")
