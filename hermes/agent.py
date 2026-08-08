"""헤르메스 에이전트 — 매매 봇을 제어하는 계층.

제어 명령(시작/정지/일시정지/재개/설정변경/긴급청산)을 한곳으로 모아
상태 머신으로 검증하고, 실행 결과를 감사 로그로 남긴다.

명령은 GUI 버튼, HTTP API, 텔레그램 등 여러 경로에서 동시에 들어올 수 있으므로
모든 명령 처리를 하나의 락으로 직렬화한다. 두 곳에서 동시에 start를 눌러
매매 스레드가 두 개 뜨는 상황을 막기 위함이다.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Optional

from .backend import TradingBackend
from .config import ParamError, RuntimeConfig
from .state import AgentState, InvalidTransition, StateMachine

# 킬 스위치가 걸린 뒤에는 이 사유가 해제(reset)될 때까지 start를 막는다.
KILL_SWITCH_REASON = "kill_switch"


class HermesAgent:
    """매매 백엔드에 대한 단일 제어 지점."""

    def __init__(
        self,
        backend: TradingBackend,
        config: Optional[RuntimeConfig] = None,
        name: str = "hermes",
        logger: Optional[Callable[[str], None]] = None,
        audit_limit: int = 200,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.name = name
        self.backend = backend
        self.config = config if config is not None else RuntimeConfig()
        self._logger = logger
        self._clock = clock
        self._lock = threading.RLock()
        self._machine = StateMachine()
        self._audit: Deque[dict[str, Any]] = deque(maxlen=audit_limit)
        self._last_error: Optional[str] = None
        self._locked_reason: Optional[str] = None
        self._started_at: Optional[float] = None

    # ------------------------- 내부 도구 -------------------------

    def _log(self, message: str) -> None:
        if self._logger is not None:
            self._logger(f"[{self.name}] {message}")

    def _record(self, command: str, ok: bool, message: str, **extra: Any) -> dict[str, Any]:
        """명령 실행 결과를 감사 로그에 남기고, 그 결과를 그대로 돌려준다."""
        entry = {
            "ts": self._clock(),
            "command": command,
            "ok": ok,
            "state": self._machine.state.value,
            "message": message,
        }
        entry.update(extra)
        self._audit.append(entry)
        self._log(("✅ " if ok else "❌ ") + f"{command}: {message}")
        return entry

    # ------------------------- 조회 -------------------------

    @property
    def state(self) -> AgentState:
        return self._machine.state

    def status(self, include_market: bool = True) -> dict[str, Any]:
        """에이전트 현재 상태 요약.

        include_market=False면 거래소 조회를 건너뛴다 (네트워크 없이 상태만 볼 때).
        """
        with self._lock:
            state = self._machine.state
            snapshot = self.config.snapshot()
            uptime = None if self._started_at is None else self._clock() - self._started_at
            payload: dict[str, Any] = {
                "name": self.name,
                "state": state.value,
                "alive": self.backend.is_alive(),
                "uptime_sec": uptime,
                "config": snapshot,
                "last_error": self._last_error,
                "locked_reason": self._locked_reason,
            }

        if include_market:
            # 거래소 조회는 락 밖에서 한다. 네트워크가 느릴 때
            # 다른 제어 명령까지 같이 막히면 안 되기 때문.
            try:
                payload["position"] = self.backend.get_position()
            except Exception as exc:  # 조회 실패가 상태 확인 자체를 막지는 않게 한다
                payload["position"] = None
                payload["position_error"] = str(exc)
            try:
                payload["balance_usdt"] = self.backend.get_balance()
            except Exception as exc:
                payload["balance_usdt"] = None
                payload["balance_error"] = str(exc)

        return payload

    def audit_log(self, limit: int = 20) -> list[dict[str, Any]]:
        """최근 제어 명령 이력 (최신 것이 마지막)."""
        with self._lock:
            entries = list(self._audit)
        return entries[-limit:] if limit > 0 else entries

    # ------------------------- 제어 명령 -------------------------

    def start(self) -> dict[str, Any]:
        """매매 루프를 기동한다."""
        with self._lock:
            if self._locked_reason is not None:
                return self._record(
                    "start", False,
                    f"에이전트가 잠겨 있습니다 ({self._locked_reason}). reset 후 다시 시도하세요.",
                )
            try:
                self._machine.to(AgentState.STARTING)
            except InvalidTransition as exc:
                return self._record("start", False, str(exc))

            try:
                self.config.validate_for_start()
            except ParamError as exc:
                self._machine.to(AgentState.STOPPED)
                return self._record("start", False, str(exc))

            # 기동 시점에는 항상 신규 진입을 허용 상태로 되돌린다.
            # 이전에 pause 상태로 정지했더라도 start는 정상 매매로 시작해야 한다.
            self.config.set("entries_enabled", True)
            snapshot = self.config.snapshot()

            try:
                self.backend.start(snapshot)
            except Exception as exc:
                self._last_error = str(exc)
                self._machine.to(AgentState.ERROR)
                return self._record("start", False, f"기동 실패: {exc}")

            self._last_error = None
            self._started_at = self._clock()
            self._machine.to(AgentState.RUNNING)
            return self._record("start", True, "매매를 시작했습니다.", config=snapshot)

    def stop(self) -> dict[str, Any]:
        """매매 루프를 정지한다 (포지션은 건드리지 않는다)."""
        with self._lock:
            try:
                self._machine.to(AgentState.STOPPING)
            except InvalidTransition as exc:
                return self._record("stop", False, str(exc))

            try:
                self.backend.stop()
            except Exception as exc:
                self._last_error = str(exc)
                self._machine.to(AgentState.ERROR)
                return self._record("stop", False, f"정지 실패: {exc}")

            self._started_at = None
            self._machine.to(AgentState.STOPPED)
            return self._record(
                "stop", True,
                "매매를 정지했습니다. (보유 포지션은 그대로 남아 있습니다)",
            )

    def pause(self) -> dict[str, Any]:
        """신규 진입만 차단한다.

        루프는 계속 돌기 때문에 보유 중인 포지션의 트레일링 스탑 관리는 유지된다.
        '더 이상 새로 들어가진 말고, 갖고 있는 건 계속 지켜봐라'에 해당한다.
        """
        with self._lock:
            try:
                self._machine.to(AgentState.PAUSED)
            except InvalidTransition as exc:
                return self._record("pause", False, str(exc))

            self.config.set("entries_enabled", False)
            return self._record(
                "pause", True,
                "신규 진입을 차단했습니다. (보유 포지션 관리는 계속됩니다)",
            )

    def resume(self) -> dict[str, Any]:
        """일시정지를 풀고 신규 진입을 다시 허용한다."""
        with self._lock:
            try:
                self._machine.to(AgentState.RUNNING)
            except InvalidTransition as exc:
                return self._record("resume", False, str(exc))

            self.config.set("entries_enabled", True)
            return self._record("resume", True, "신규 진입을 다시 허용합니다.")

    def set_param(self, name: str, value: Any) -> dict[str, Any]:
        """설정값 하나를 실시간으로 변경한다.

        매매 루프는 매 주기마다 설정을 다시 읽으므로 다음 주기부터 반영된다.
        """
        with self._lock:
            try:
                coerced = self.config.set(name, value)
            except ParamError as exc:
                return self._record("set_param", False, str(exc), param=name)

            return self._record(
                "set_param", True,
                f"{name} = {coerced} (다음 판단 주기부터 적용)",
                param=name, value=coerced,
            )

    def set_params(self, values: dict[str, Any]) -> dict[str, Any]:
        """여러 설정값을 원자적으로 변경한다."""
        with self._lock:
            try:
                coerced = self.config.update(values)
            except ParamError as exc:
                return self._record("set_params", False, str(exc))

            summary = ", ".join(f"{k}={v}" for k, v in sorted(coerced.items()))
            return self._record("set_params", True, f"{summary} (다음 판단 주기부터 적용)", values=coerced)

    def kill_switch(self, reason: str = "manual") -> dict[str, Any]:
        """긴급 정지 — 보유 포지션을 전량 시장가 청산하고 루프를 멈춘다.

        가장 위험한 명령이므로 다른 명령과 달리 상태 전이 규칙에 막히지 않는다.
        어떤 상태에서 호출하든 '멈추는 것'이 최우선이다.

        실행 후에는 에이전트가 잠기며, reset()을 명시적으로 호출해야 다시 시작할 수 있다.
        실수로 재기동되는 것을 막기 위한 안전장치다.
        """
        with self._lock:
            # 1) 무엇보다 먼저 신규 진입을 막는다. 청산 도중에 루프가
            #    새 포지션을 여는 경합을 없애기 위함이다.
            self.config.set("entries_enabled", False)

            errors: list[str] = []
            flatten_result: dict[str, Any] | None = None

            # 2) 포지션 청산
            try:
                flatten_result = self.backend.flatten()
            except Exception as exc:
                errors.append(f"청산 실패: {exc}")

            # 3) 루프 정지 — 청산이 실패해도 정지는 반드시 시도한다.
            try:
                self.backend.stop()
            except Exception as exc:
                errors.append(f"정지 실패: {exc}")

            self._started_at = None
            self._locked_reason = KILL_SWITCH_REASON

            if errors:
                self._last_error = "; ".join(errors)
                self._machine.force(AgentState.ERROR)
                return self._record(
                    "kill_switch", False,
                    f"긴급 정지 중 문제 발생 — 거래소에서 직접 확인하세요: {self._last_error}",
                    reason=reason, flatten=flatten_result,
                )

            self._machine.force(AgentState.STOPPED)
            return self._record(
                "kill_switch", True,
                f"긴급 정지 완료 (사유: {reason}). 포지션 청산 및 매매 정지됨. "
                "다시 시작하려면 reset이 필요합니다.",
                reason=reason, flatten=flatten_result,
            )

    def reset(self) -> dict[str, Any]:
        """ERROR 상태나 킬 스위치 잠금을 해제해 다시 start 할 수 있게 만든다."""
        with self._lock:
            if self._machine.state in (AgentState.RUNNING, AgentState.PAUSED, AgentState.STARTING):
                return self._record(
                    "reset", False,
                    "매매가 동작 중입니다. 먼저 stop 후에 reset 하세요.",
                )

            self._machine.force(AgentState.STOPPED)
            previous_error = self._last_error
            self._last_error = None
            self._locked_reason = None
            return self._record("reset", True, "에이전트를 초기화했습니다.", cleared_error=previous_error)
