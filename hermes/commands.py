"""명령 해석 계층.

HTTP API, 텔레그램 명령어, CLI가 모두 같은 명령 집합을 쓰도록 한곳에 모았다.
입력이 전부 문자열로 들어오는 텔레그램/CLI를 위해 문자열 인자 파싱도 여기서 처리한다.

    dispatch(agent, "set", ["leverage", "5"])
    dispatch(agent, "status", [])
"""

from __future__ import annotations

from typing import Any, Callable, NamedTuple, Sequence

from .agent import HermesAgent
from .config import PARAM_SPECS


class CommandError(ValueError):
    """명령 이름이나 인자가 잘못됐을 때 발생."""


class Command(NamedTuple):
    handler: Callable[[HermesAgent, Sequence[str]], dict[str, Any]]
    usage: str
    help: str
    mutating: bool  # 매매에 실제 영향을 주는 명령인지 (읽기 전용 토큰과 구분할 때 사용)


def _no_args(name: str, args: Sequence[str]) -> None:
    if args:
        raise CommandError(f"{name} 명령은 인자를 받지 않습니다.")


def _cmd_status(agent: HermesAgent, args: Sequence[str]) -> dict[str, Any]:
    # status market 이라고 하면 거래소 조회까지 포함한다. 기본은 조회 포함.
    include_market = not (args and args[0].lower() in ("local", "quick", "nomarket"))
    return {"ok": True, "command": "status", "status": agent.status(include_market=include_market)}


def _cmd_start(agent: HermesAgent, args: Sequence[str]) -> dict[str, Any]:
    _no_args("start", args)
    return agent.start()


def _cmd_stop(agent: HermesAgent, args: Sequence[str]) -> dict[str, Any]:
    _no_args("stop", args)
    return agent.stop()


def _cmd_pause(agent: HermesAgent, args: Sequence[str]) -> dict[str, Any]:
    _no_args("pause", args)
    return agent.pause()


def _cmd_resume(agent: HermesAgent, args: Sequence[str]) -> dict[str, Any]:
    _no_args("resume", args)
    return agent.resume()


def _cmd_reset(agent: HermesAgent, args: Sequence[str]) -> dict[str, Any]:
    _no_args("reset", args)
    return agent.reset()


def _cmd_set(agent: HermesAgent, args: Sequence[str]) -> dict[str, Any]:
    if len(args) != 2:
        raise CommandError("사용법: set <항목> <값>  (예: set leverage 5)")
    return agent.set_param(args[0], args[1])


def _cmd_params(agent: HermesAgent, args: Sequence[str]) -> dict[str, Any]:
    _no_args("params", args)
    return {
        "ok": True,
        "command": "params",
        "current": agent.config.snapshot(),
        "spec": {name: spec.describe for name, spec in PARAM_SPECS.items()},
    }


def _cmd_kill(agent: HermesAgent, args: Sequence[str]) -> dict[str, Any]:
    # 오조작 방지를 위해 확인 문구를 요구한다.
    if not args or args[0].upper() != "CONFIRM":
        raise CommandError(
            "긴급 청산은 되돌릴 수 없습니다. 실행하려면 'kill CONFIRM' 으로 입력하세요."
        )
    reason = " ".join(args[1:]) or "manual"
    return agent.kill_switch(reason=reason)


def _cmd_log(agent: HermesAgent, args: Sequence[str]) -> dict[str, Any]:
    limit = 20
    if args:
        try:
            limit = int(args[0])
        except ValueError:
            raise CommandError("사용법: log [개수]") from None
    return {"ok": True, "command": "log", "entries": agent.audit_log(limit)}


COMMANDS: dict[str, Command] = {
    "status": Command(_cmd_status, "status [local]", "현재 상태·포지션·잔고 조회", False),
    "params": Command(_cmd_params, "params", "설정 가능한 항목과 현재 값 조회", False),
    "log": Command(_cmd_log, "log [개수]", "최근 제어 명령 이력 조회", False),
    "start": Command(_cmd_start, "start", "자동매매 시작", True),
    "stop": Command(_cmd_stop, "stop", "자동매매 정지 (포지션은 유지)", True),
    "pause": Command(_cmd_pause, "pause", "신규 진입만 차단 (보유 포지션 관리는 계속)", True),
    "resume": Command(_cmd_resume, "resume", "신규 진입 재개", True),
    "set": Command(_cmd_set, "set <항목> <값>", "설정값 실시간 변경", True),
    "kill": Command(_cmd_kill, "kill CONFIRM [사유]", "긴급 청산 + 정지 (되돌릴 수 없음)", True),
    "reset": Command(_cmd_reset, "reset", "오류/킬스위치 잠금 해제", True),
}


def dispatch(agent: HermesAgent, name: str, args: Sequence[str] = ()) -> dict[str, Any]:
    """명령 하나를 실행하고 결과 딕셔너리를 돌려준다."""
    command = COMMANDS.get(name.strip().lower())
    if command is None:
        raise CommandError(f"알 수 없는 명령입니다: {name}\n{help_text()}")
    return command.handler(agent, list(args))


def parse_and_dispatch(agent: HermesAgent, line: str) -> dict[str, Any]:
    """'set leverage 5' 같은 한 줄 문자열을 실행한다 (텔레그램/CLI용)."""
    tokens = line.strip().lstrip("/").split()
    if not tokens:
        raise CommandError(help_text())
    return dispatch(agent, tokens[0], tokens[1:])


def help_text() -> str:
    lines = ["사용 가능한 명령:"]
    for command in COMMANDS.values():
        lines.append(f"  {command.usage:<24} {command.help}")
    return "\n".join(lines)
