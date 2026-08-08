"""기존 tkinter 매매 봇(GateioProSuperTrendBot)을 헤르메스 백엔드로 감싸는 어댑터.

기존 봇은 GUI와 매매 로직이 한 클래스에 붙어 있고, 제어 메서드(start_bot/stop_bot)가
위젯 상태를 직접 건드린다. tkinter 위젯은 메인 스레드에서만 만져야 하므로,
HTTP 서버나 텔레그램 스레드에서 들어온 제어 명령은 반드시 UI 스레드로 넘겨야 한다.
그 스레드 전환을 이 어댑터가 담당한다.

봇 코드 자체는 거의 건드리지 않는다. 어댑터가 봇에 runtime_config를 주입하면
매매 루프가 매 주기마다 그 설정을 읽어가고, 주입하지 않으면 기존 동작 그대로다.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Mapping, Optional

from .backend import BackendError
from .config import RuntimeConfig

# UI 스레드에 넘긴 작업이 이 시간 안에 끝나지 않으면 실패로 본다.
# 거래소 연동(init_exchange)이 포함될 수 있어 넉넉히 잡는다.
UI_CALL_TIMEOUT_SEC = 60.0


class TkBotBackend:
    """GateioProSuperTrendBot 인스턴스를 TradingBackend 인터페이스로 노출한다."""

    def __init__(self, bot: Any, config: Optional[RuntimeConfig] = None) -> None:
        self.bot = bot
        self.config = config if config is not None else RuntimeConfig()

        # 어댑터는 GUI 구성 시점(=UI 스레드)에서 만들어진다고 가정하고
        # 그 스레드를 기억해 둔다. 같은 스레드에서 온 호출은 곧바로 실행한다.
        self._ui_thread = threading.current_thread()

        # 매매 루프가 설정을 읽어갈 수 있도록 봇에 주입한다.
        self.bot.runtime_config = self.config

    # ------------------------- 스레드 전환 -------------------------

    def _on_ui(self, fn: Callable[[], Any], timeout: float = UI_CALL_TIMEOUT_SEC) -> Any:
        """fn을 tkinter 메인 스레드에서 실행하고 결과를 돌려준다."""
        if threading.current_thread() is self._ui_thread:
            return fn()

        done = threading.Event()
        box: dict[str, Any] = {}

        def runner() -> None:
            try:
                box["value"] = fn()
            except BaseException as exc:  # noqa: BLE001 — 예외를 호출한 쪽으로 그대로 전달
                box["error"] = exc
            finally:
                done.set()

        self.bot.root.after(0, runner)
        if not done.wait(timeout):
            raise BackendError(f"UI 스레드 응답이 {timeout:.0f}초 안에 오지 않았습니다.")
        if "error" in box:
            raise BackendError(f"UI 스레드에서 오류 발생: {box['error']}") from box["error"]
        return box.get("value")

    # ------------------------- 설정 반영 -------------------------

    def _apply_to_widgets(self, config: Mapping[str, Any]) -> None:
        """설정값을 GUI 위젯에 써 넣는다 (반드시 UI 스레드에서 호출).

        기존 start_bot()이 위젯 값을 직접 읽어 검증하기 때문에, 원격에서 바꾼
        설정이 그 검증을 통과하려면 위젯에도 같은 값이 들어가 있어야 한다.
        """
        bot = self.bot

        bot.var_amount_mode.set(config["amount_mode"])

        if config["fixed_amount_usdt"] is not None:
            bot.entry_amount.config(state="normal")
            bot.entry_amount.delete(0, "end")
            bot.entry_amount.insert(0, str(config["fixed_amount_usdt"]))

        if config["balance_pct"] is not None:
            bot.entry_balance_pct.config(state="normal")
            bot.entry_balance_pct.delete(0, "end")
            bot.entry_balance_pct.insert(0, str(config["balance_pct"]))

        bot.spin_leverage.delete(0, "end")
        bot.spin_leverage.insert(0, str(config["leverage"]))

        # 선택된 모드에 맞게 입력칸 활성/비활성 상태를 정리한다.
        bot.update_amount_mode_ui()

    # ------------------------- TradingBackend 구현 -------------------------

    def start(self, config: Mapping[str, Any]) -> None:
        def do_start() -> bool:
            self._apply_to_widgets(config)
            self.bot.start_bot()
            # start_bot은 실패해도 예외를 던지지 않고 messagebox만 띄운 뒤 돌아온다.
            # 성공 여부는 is_running 플래그로 판단한다.
            return bool(self.bot.is_running)

        started = self._on_ui(do_start)
        if not started:
            raise BackendError(
                "매매 기동에 실패했습니다. API 키, 주문 금액 설정, 거래소 연동 상태를 확인하세요."
            )

    def stop(self) -> None:
        self._on_ui(self.bot.stop_bot)

    def is_alive(self) -> bool:
        if not getattr(self.bot, "is_running", False):
            return False
        thread = getattr(self.bot, "trade_thread", None)
        # 스레드 정보가 없으면 is_running 플래그를 그대로 신뢰한다.
        return True if thread is None else bool(thread.is_alive())

    def get_position(self) -> dict[str, Any]:
        # ccxt 호출은 스레드 안전하므로 UI 스레드로 넘기지 않는다.
        # (넘기면 네트워크 지연 동안 GUI가 멈춘다)
        self._require_exchange()
        return self.bot.get_current_position()

    def get_balance(self) -> float | None:
        self._require_exchange()
        return self.bot.fetch_futures_balance()

    def flatten(self) -> dict[str, Any]:
        """보유 포지션을 전량 시장가 청산한다."""
        self._require_exchange()

        position = self.bot.get_current_position()
        if position["side"] == "none" or position["size"] <= 0:
            return {"closed": False, "side": "none", "size": 0.0, "detail": "청산할 포지션이 없습니다."}

        close_side = "sell" if position["side"] == "long" else "buy"
        order = self.bot.execute_market_order(close_side, position["size"], reduce_only=True)
        if order is None:
            raise BackendError(
                f"{position['side'].upper()} 포지션 {position['size']} 계약 청산 주문이 거부됐습니다. "
                "거래소에서 직접 확인하세요."
            )

        # 체결가를 기록해 두면 거래 내역에서 킬 스위치 청산을 추적할 수 있다.
        exit_price = position["entry_price"]
        try:
            exit_price = float(self.bot.exchange.fetch_ticker(self.bot.symbol)["last"])
        except Exception:
            pass  # 체결가 조회 실패가 청산 자체를 실패로 만들지는 않는다

        try:
            self.bot.record_trade(
                position["side"], position["entry_price"], exit_price,
                "긴급청산(킬스위치)", position["size"],
            )
        except Exception:
            pass  # 기록 실패도 청산 결과에 영향을 주지 않는다

        return {
            "closed": True,
            "side": position["side"],
            "size": position["size"],
            "exit_price": exit_price,
            "detail": f"{position['side'].upper()} {position['size']} 계약을 시장가로 청산했습니다.",
        }

    # ------------------------- 보조 -------------------------

    def _require_exchange(self) -> None:
        if getattr(self.bot, "exchange", None) is None:
            raise BackendError("거래소에 아직 연동되지 않았습니다. 먼저 매매를 시작하세요.")
