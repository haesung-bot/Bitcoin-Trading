"""헤르메스 — 자동매매 봇 제어 계층.

기존 매매 봇은 GUI 버튼으로만 조작할 수 있었다. 헤르메스는 그 위에
'제어'라는 얇은 계층을 하나 얹어서 다음을 가능하게 한다.

  * 시작 / 정지 / 일시정지 / 재개를 GUI 밖에서도 호출
  * 상태·포지션·잔고 조회
  * 레버리지, 주문 금액 같은 설정을 봇 재시작 없이 실시간 변경
  * 긴급 청산(킬 스위치) — 보유 포지션 전량 정리 후 정지

가장 간단한 사용법 (tkinter 봇에 붙이기):

    from hermes import attach

    app = GateioProSuperTrendBot(root)
    control = attach(app, port=8787)     # 로컬 HTTP 제어 API가 함께 뜬다
    print(control.url, control.server.token)

제어 API 없이 에이전트만 쓰고 싶다면 start_server=False를 준다.
"""

from __future__ import annotations

from typing import Any, Callable, NamedTuple, Optional

from .agent import HermesAgent
from .backend import BackendError, TradingBackend
from .commands import CommandError, COMMANDS, dispatch, help_text, parse_and_dispatch
from .config import ParamError, RuntimeConfig
from .control_server import ControlServer
from .state import AgentState, InvalidTransition, StateMachine

__all__ = [
    "AgentState",
    "BackendError",
    "COMMANDS",
    "CommandError",
    "ControlServer",
    "HermesAgent",
    "HermesControl",
    "InvalidTransition",
    "ParamError",
    "RuntimeConfig",
    "StateMachine",
    "TradingBackend",
    "attach",
    "dispatch",
    "help_text",
    "parse_and_dispatch",
]

__version__ = "1.0.0"


class HermesControl(NamedTuple):
    """attach()가 돌려주는 묶음."""

    agent: HermesAgent
    config: RuntimeConfig
    server: Optional[ControlServer]
    url: Optional[str]


def attach(
    bot: Any,
    *,
    start_server: bool = True,
    host: str = "127.0.0.1",
    port: int = 8787,
    token: Optional[str] = None,
    allow_remote: bool = False,
    logger: Optional[Callable[[str], None]] = None,
    **initial_params: Any,
) -> HermesControl:
    """tkinter 매매 봇 인스턴스에 헤르메스를 붙인다.

    반드시 UI 스레드(봇을 만든 그 스레드)에서 호출해야 한다. 어댑터가 이 시점의
    스레드를 tkinter 메인 스레드로 기억하기 때문이다.

    initial_params로 leverage, amount_mode 등 초기 설정을 함께 넘길 수 있다.
    """
    from .adapter import TkBotBackend  # 순환 import 방지를 위해 지연 import

    # 로거를 안 주면 봇의 로그창에 그대로 찍는다.
    if logger is None and hasattr(bot, "log"):
        logger = bot.log

    config = RuntimeConfig(**initial_params) if initial_params else RuntimeConfig()
    backend = TkBotBackend(bot, config=config)
    agent = HermesAgent(backend, config=config, logger=logger)

    server: Optional[ControlServer] = None
    url: Optional[str] = None
    if start_server:
        server = ControlServer(
            agent, host=host, port=port, token=token,
            allow_remote=allow_remote, logger=logger,
        )
        url = server.start()

    return HermesControl(agent=agent, config=config, server=server, url=url)
