"""헤르메스 에이전트의 상태 머신.

에이전트 제어는 '지금 어떤 상태인가'에 따라 허용되는 명령이 달라진다.
예를 들어 이미 RUNNING인 에이전트를 다시 start 하거나, STOPPED 상태에서
pause 하는 것은 잘못된 요청이므로 여기서 막는다.

거래소 API나 tkinter에 의존하지 않는 순수 파이썬 모듈이라 단독으로 테스트할 수 있다.
"""

from __future__ import annotations

import threading
from enum import Enum
from typing import Callable, Optional


class AgentState(str, Enum):
    """에이전트가 가질 수 있는 상태."""

    STOPPED = "stopped"    # 매매 루프가 돌지 않음 (초기 상태)
    STARTING = "starting"  # 거래소 연동 등 기동 준비 중
    RUNNING = "running"    # 매매 루프 동작 중 (신규 진입 허용)
    PAUSED = "paused"      # 루프는 살아있지만 신규 진입만 차단 (보유 포지션 관리는 계속)
    STOPPING = "stopping"  # 정지 요청을 받고 루프가 빠져나오는 중
    ERROR = "error"        # 기동 실패 등 비정상 종료 — reset 해야 다시 start 가능


class InvalidTransition(RuntimeError):
    """현재 상태에서 허용되지 않는 상태 전이를 시도했을 때 발생."""

    def __init__(self, current: AgentState, target: AgentState) -> None:
        super().__init__(
            f"'{current.value}' 상태에서는 '{target.value}'(으)로 전이할 수 없습니다."
        )
        self.current = current
        self.target = target


# 상태별로 넘어갈 수 있는 다음 상태 목록.
_ALLOWED: dict[AgentState, frozenset[AgentState]] = {
    AgentState.STOPPED: frozenset({AgentState.STARTING}),
    AgentState.STARTING: frozenset({AgentState.RUNNING, AgentState.STOPPED, AgentState.ERROR}),
    AgentState.RUNNING: frozenset({AgentState.PAUSED, AgentState.STOPPING, AgentState.ERROR}),
    AgentState.PAUSED: frozenset({AgentState.RUNNING, AgentState.STOPPING, AgentState.ERROR}),
    AgentState.STOPPING: frozenset({AgentState.STOPPED, AgentState.ERROR}),
    AgentState.ERROR: frozenset({AgentState.STOPPED}),
}

# 매매 루프가 실제로 살아있는(스레드가 도는) 상태들.
LIVE_STATES = frozenset({AgentState.STARTING, AgentState.RUNNING, AgentState.PAUSED, AgentState.STOPPING})


class StateMachine:
    """스레드 안전한 상태 머신.

    제어 명령은 GUI 스레드, HTTP 서버 스레드, 매매 루프 스레드 등 여러 곳에서
    동시에 들어올 수 있으므로 모든 접근을 락으로 감싼다.
    """

    def __init__(
        self,
        initial: AgentState = AgentState.STOPPED,
        on_change: Optional[Callable[[AgentState, AgentState], None]] = None,
    ) -> None:
        self._state = initial
        self._lock = threading.RLock()
        self._on_change = on_change

    @property
    def state(self) -> AgentState:
        with self._lock:
            return self._state

    def is_live(self) -> bool:
        """매매 루프가 살아있다고 간주되는 상태인지."""
        return self.state in LIVE_STATES

    def can(self, target: AgentState) -> bool:
        with self._lock:
            return target in _ALLOWED[self._state]

    def to(self, target: AgentState) -> AgentState:
        """상태를 전이한다. 허용되지 않으면 InvalidTransition을 던진다."""
        with self._lock:
            current = self._state
            if target not in _ALLOWED[current]:
                raise InvalidTransition(current, target)
            self._state = target

        # 콜백은 락 밖에서 호출한다. 콜백이 다시 상태 머신을 건드려도
        # 데드락이 나지 않도록 하기 위함.
        if self._on_change is not None:
            self._on_change(current, target)
        return target

    def force(self, target: AgentState) -> AgentState:
        """전이 규칙을 무시하고 상태를 강제로 바꾼다.

        킬 스위치처럼 '무슨 일이 있어도 멈춰야 하는' 경로에서만 사용한다.
        """
        with self._lock:
            current = self._state
            self._state = target

        if self._on_change is not None and current != target:
            self._on_change(current, target)
        return target
