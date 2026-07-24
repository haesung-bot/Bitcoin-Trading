"""
hedged_martingale_bot.py
비트코인 10배 레버리지 양방향(Hedge Mode) 마틴게일 자동매매 모듈

- 기본 실행 모드: 모의 매매(Paper Trading). 실거래는 --live 옵션 + API 키가 있을 때만 동작.
- 기본 거래소는 Gate.io 무기한 선물(USDT-M swap). EXCHANGE_ID 환경변수로 다른 ccxt 지원 거래소로 변경 가능.
- 롱/숏은 완전히 독립된 상태머신(MartingaleModule)이며 서로의 진입/청산에 영향을 주지 않는다.
- 시세(15분봉 종가)는 실제 매매할 거래소(EXCHANGE_ID)에서 ccxt 공개 API로 직접 가져온다(인증 불필요).
- 실거래 시에만 ccxt로 Hedge Mode(Gate.io는 Dual Mode) + 레버리지 10배를 계좌에 설정한다.
- Gate.io Dual Mode는 매수/매도 방향이 곧 롱/숏 슬롯을 가리키고, 청산 주문에만 reduceOnly를 붙인다.
  바이낸스 Hedge Mode는 반대로 positionSide로 방향을 지정하고 reduceOnly는 보내면 안 된다(거래소별 분기 처리).
- 추세장에서 마틴게일이 반복 손절되는 것을 막기 위해, 연속 손절이 MAX_CONSECUTIVE_SL(기본 3)회
  누적되면 해당 방향(롱 또는 숏)만 자동 정지되고 텔레그램으로 통지된다(수동 재시작 전까지 재진입 안 함).
- 실거래/모의매매 루프는 매 틱마다 롱/숏의 진행 상태(진입 단계, 체결 내역, 쿨다운, 연속손절 횟수)를
  STATE_PATH 파일에 저장한다. 프로그램을 껐다 켜도 이 파일에서 상태를 복원해 이어서 진행하며,
  실거래에서는 재시작 시 거래소의 실제 포지션 수량과 저장된 상태를 대조해 어긋나면 경고한다.

사전 준비: pip install ccxt requests  (--selftest만 쓸 경우 ccxt 없이도 동작)

사용법:
    python hedged_martingale_bot.py --selftest      # 가상 차트로 진입 조건 정성 테스트(네트워크 불필요)
    python hedged_martingale_bot.py                 # 모의 매매로 실시간 루프 실행 (Gate.io 실시세 사용)
    python hedged_martingale_bot.py --live           # 실거래 (EXCHANGE_API_KEY/SECRET 환경변수 필요)
"""

from __future__ import annotations

# 실행 중인 코드가 최신인지 로그로 바로 확인하기 위한 버전 표식.
# 코드를 의미 있게 바꿀 때마다 이 문자열을 갱신한다.
VERSION = "2026-07-24-e (자동동기화+상태초기화+버전표시)"

import argparse
import json
import logging
import math
import os
import random
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, List, Optional, Tuple

import requests

try:
    import ccxt
except ImportError:
    ccxt = None

# ccxt 버전에 따라 거래소 클래스 이름이 다를 수 있다(예: Gate.io는 gate/gateio).
_EXCHANGE_CLASS_ALIASES = {
    "gate": ("gate", "gateio"),
    "gateio": ("gate", "gateio"),
}


def _resolve_exchange_class(exchange_id: str):
    if ccxt is None:
        raise RuntimeError("ccxt가 설치되어 있지 않습니다. 'pip install ccxt'를 실행하세요.")
    for candidate in _EXCHANGE_CLASS_ALIASES.get(exchange_id, (exchange_id,)):
        cls = getattr(ccxt, candidate, None)
        if cls is not None:
            return cls
    raise RuntimeError(f"ccxt에서 거래소 '{exchange_id}'를 찾을 수 없습니다. 'pip install -U ccxt'로 업데이트하세요.")


# ───────────── 전략 설정값 ─────────────
EXCHANGE_ID = os.environ.get("EXCHANGE_ID", "gateio")   # gateio, binance 등 ccxt 지원 거래소
SYMBOL = os.environ.get("SYMBOL", "BTC/USDT:USDT")
TIMEFRAME = "15m"
LEVERAGE = 10
INITIAL_MARGIN_PCT = 0.02       # 1차 진입 마진 = 계좌 잔고의 2%
STEP_TRIGGER_PCT = 0.003        # 평단가 대비 0.3% 역방향 이동 시 물타기/손절 트리거
TP_PCT = 0.003                  # 평단가 대비 0.3% 순방향 이동 시 익절
MAX_STEPS = 4                   # 1배 -> 2배 -> 4배 -> 8배
COOLDOWN_SEC = 180              # 청산 후 재진입 대기 3분
MAX_CONSECUTIVE_SL = int(os.environ.get("MAX_CONSECUTIVE_SL", "3"))  # 연속 손절 N회 시 해당 방향 자동 정지
RSI_PERIOD = 14
RSI_LONG_TRIGGER = 40.0
RSI_SHORT_TRIGGER = 60.0
BB_PERIOD = 20
BB_STDDEV_MULT = 2.0
PAPER_START_BALANCE = float(os.environ.get("PAPER_START_BALANCE", "10000"))
POLL_SEC = int(os.environ.get("POLL_SEC", "30"))  # 가격 조회 주기(초)
STATE_PATH = os.environ.get(
    "STATE_PATH", os.path.join(os.path.expanduser("~"), ".hedged_martingale_bot_state.json")
)  # 진행 중인 매매 상태(진입 단계/체결 내역/쿨다운) 저장 위치. 재시작 시 여기서 복원한다.

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("hedged_martingale_bot")


# ───────────── 지표 계산 (pandas/numpy 없이 순수 파이썬) ─────────────
class Indicators:
    @staticmethod
    def rsi(closes: List[float], period: int = RSI_PERIOD) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    @staticmethod
    def bollinger(
        closes: List[float], period: int = BB_PERIOD, mult: float = BB_STDDEV_MULT
    ) -> Optional[Tuple[float, float, float]]:
        if len(closes) < period:
            return None
        window = closes[-period:]
        mid = sum(window) / period
        variance = sum((c - mid) ** 2 for c in window) / period
        std = math.sqrt(variance)
        return mid, mid + mult * std, mid - mult * std


# ───────────── 텔레그램 알림 ─────────────
class TelegramNotifier:
    def __init__(self, token: str = TELEGRAM_BOT_TOKEN, chat_id: str = TELEGRAM_CHAT_ID):
        self.token = token
        self.chat_id = chat_id

    def send(self, text: str) -> None:
        logger.info("[알림] %s", text.replace("\n", " | "))
        if not self.token or not self.chat_id:
            logger.warning("텔레그램 토큰 또는 Chat ID가 비어 있어 메시지를 보내지 않습니다. GUI에 두 값을 입력했는지 확인하세요.")
            return
        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            resp = requests.post(url, data={"chat_id": self.chat_id, "text": text}, timeout=10)
            body = resp.json()
            if not body.get("ok"):
                # 텔레그램이 왜 거부했는지(예: chat not found, 봇에게 먼저 /start 안 함)를 그대로 보여준다.
                logger.warning(
                    "텔레그램 전송 실패 (code=%s): %s | Chat ID나 토큰이 맞는지, 봇에게 먼저 /start를 보냈는지 확인하세요.",
                    body.get("error_code"), body.get("description"),
                )
        except Exception as e:
            logger.warning("텔레그램 전송 오류(네트워크/방화벽 확인): %s", e)


# ───────────── 시세 데이터 (공개 API, 인증 불필요) ─────────────
class PublicMarketData:
    """실제 매매할 거래소(EXCHANGE_ID)와 동일한 곳에서 ccxt 공개 API로 캔들을 가져온다. API 키 불필요."""

    def __init__(self, exchange_id: str = EXCHANGE_ID, symbol: str = SYMBOL, timeframe: str = TIMEFRAME, limit: int = 100):
        exchange_cls = _resolve_exchange_class(exchange_id)
        self.exchange = exchange_cls({"enableRateLimit": True, "options": {"defaultType": "swap"}})
        self.symbol = symbol
        self.timeframe = timeframe
        self.limit = limit

    def get_closes(self) -> List[float]:
        candles = self.exchange.fetch_ohlcv(self.symbol, timeframe=self.timeframe, limit=self.limit)
        return [float(c[4]) for c in candles]


# ───────────── 브로커(체결/잔고) ─────────────
class Side(Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class PaperBroker:
    """모의 매매 전용 가상 체결/잔고 관리자. 실제 주문을 전송하지 않는다."""

    def __init__(self, starting_balance: float = PAPER_START_BALANCE):
        self.balance = starting_balance

    def get_balance(self) -> float:
        return self.balance

    def quantize_qty(self, qty_btc: float, price: float) -> float:
        return qty_btc  # 모의매매는 거래소 계약 단위가 없으므로 계산된 수량을 그대로 사용

    def fill_order(self, side: Side, is_entry: bool, qty: float, price: float) -> None:
        logger.info(
            "[모의체결] %s %s qty=%.6f price=%.2f",
            side.value, "진입" if is_entry else "청산", qty, price,
        )

    def apply_pnl(self, pnl: float) -> None:
        self.balance += pnl

    def fetch_position(self, side: Side) -> Optional[dict]:
        return None  # 모의매매는 거래소 실포지션이 없으므로 동기화하지 않음(저장된 상태 유지)


_EXCHANGE_DEFAULT_TYPE = {
    "gate": "swap",
    "gateio": "swap",
    "binance": "future",
}


class LiveBroker:
    """실거래 브로커. 최초 연결 시 계좌에 Hedge Mode(Gate.io는 Dual Mode)와 레버리지 10배를 설정한다."""

    def __init__(self, exchange_id: str, api_key: str, api_secret: str, symbol: str = SYMBOL, leverage: int = LEVERAGE):
        exchange_cls = _resolve_exchange_class(exchange_id)
        self.exchange_id = exchange_id
        self.exchange = exchange_cls({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": _EXCHANGE_DEFAULT_TYPE.get(exchange_id, "swap")},
        })
        self.symbol = symbol
        self.exchange.load_markets()  # market()/set_position_mode()가 미리 로드된 마켓 정보를 필요로 함
        self._setup_account(leverage)

    def _setup_account(self, leverage: int) -> None:
        try:
            self.exchange.set_position_mode(hedged=True, symbol=self.symbol)
        except Exception as e:
            if "NO_CHANGE" in str(e):
                # Gate.io는 이미 설정된 값으로 다시 설정을 시도하면 에러 형태로 응답한다.
                # 즉 이미 Dual Mode가 켜져 있다는 뜻이라 정상 상태다.
                logger.info("Hedge/Dual Mode는 이미 설정되어 있습니다 (정상).")
            else:
                logger.warning("Hedge/Dual Mode 설정 실패: %s", e)
        try:
            self.exchange.set_leverage(leverage, self.symbol)
        except Exception as e:
            logger.warning("레버리지 설정 실패: %s", e)

    def _read_usdt_balance(self, params: dict) -> float:
        bal = self.exchange.fetch_balance(params)
        usdt = bal.get("USDT", {}) or {}
        free = usdt.get("free")
        total = usdt.get("total")
        # 자금이 포지션 증거금에 묶이면 free=0이 될 수 있어, free가 0이면 total로 대체한다.
        return float(free if free else (total or 0.0))

    def get_balance(self) -> float:
        # 1) 기본 선물계좌(classic futures) 조회
        try:
            balance = self._read_usdt_balance({})
        except Exception as e:
            logger.warning("잔고 조회 실패: %s", e)
            balance = 0.0
        # 2) 0이면 통합계좌(Unified Account)에 자금이 있을 수 있어 재조회
        if balance <= 0 and self.exchange_id in ("gate", "gateio"):
            try:
                balance = self._read_usdt_balance({"unifiedAccount": True})
                if balance > 0:
                    logger.info("통합계좌(Unified Account)에서 USDT 잔고를 조회했습니다: %.4f", balance)
            except Exception as e:
                logger.debug("통합계좌 잔고 조회 실패: %s", e)
        return balance

    def quantize_qty(self, qty_btc: float, price: float) -> float:
        """계산된 BTC 수량을 거래소에서 실제 주문 가능한 값으로 보정해 BTC 단위로 돌려준다.

        - 계약 스텝(예: 1계약=0.0001 BTC)에 맞춰 내림
        - 그 결과가 거래소 최소 수량(보통 1계약)보다 작으면 최소 수량으로 상향
          (계좌가 작아 2% 마진이 1계약에 못 미쳐도 매매가 진행되게 함)
        """
        market = self.exchange.market(self.symbol)
        contract_size = market.get("contractSize") or 1
        limits = market.get("limits", {}) or {}
        min_contracts = (limits.get("amount") or {}).get("min") or 1
        try:
            contracts = float(self.exchange.amount_to_precision(self.symbol, round(qty_btc / contract_size, 8)))
        except Exception:
            contracts = 0.0
        if contracts < min_contracts:
            logger.warning(
                "계산된 수량(%.8f BTC)이 거래소 최소 주문(%s계약=%.8f BTC)보다 작아 최소 수량으로 올려서 주문합니다. "
                "이 경우 실제 리스크가 잔고의 2%%보다 커질 수 있습니다.",
                qty_btc, min_contracts, min_contracts * contract_size,
            )
            contracts = float(min_contracts)
        return contracts * contract_size

    def fetch_position(self, side: Side) -> Optional[dict]:
        """재시작 시 거래소에 실제로 열려있는 롱/숏 포지션(BTC 수량 + 평단가)을 조회한다.

        반환값:
        - None            : 조회 실패(네트워크 등) → 저장된 상태를 그대로 유지해야 함
        - {"qty": 0, ...} : 해당 방향 포지션 없음(확실히 flat) → 내부 상태를 비워야 함
        - {"qty": X, ...} : 실제 포지션 존재 → 이 값으로 내부 상태를 동기화
        """
        try:
            positions = self.exchange.fetch_positions([self.symbol])
        except Exception as e:
            logger.warning("포지션 조회 실패(저장된 상태 유지): %s", e)
            return None
        contract_size = (self.exchange.market(self.symbol).get("contractSize")) or 1
        target = side.value.lower()
        for p in positions:
            p_side = (p.get("side") or "").lower()
            contracts = p.get("contracts") or 0
            if p_side == target and contracts:
                entry = p.get("entryPrice") or p.get("markPrice")
                return {
                    "qty": abs(contracts) * contract_size,
                    "entry_price": float(entry) if entry else None,
                    "contract_size": contract_size,
                }
        return {"qty": 0.0, "entry_price": None, "contract_size": contract_size}

    def _check_min_notional(self, qty_contracts: float, price: float) -> None:
        try:
            market = self.exchange.market(self.symbol)
            contract_size = market.get("contractSize") or 1
            limits = market.get("limits", {}) or {}
            min_amount = (limits.get("amount") or {}).get("min")
            min_cost = (limits.get("cost") or {}).get("min")
            if min_amount and qty_contracts < min_amount:
                logger.warning("주문 수량 %.8f계약이 거래소 최소 수량 %.8f계약보다 작습니다. 주문이 거부될 수 있습니다.", qty_contracts, min_amount)
            notional = qty_contracts * contract_size * price
            if min_cost and notional < min_cost:
                logger.warning("주문 금액 %.2f이 거래소 최소 주문금액 %.2f보다 작습니다. 주문이 거부될 수 있습니다.", notional, min_cost)
        except Exception as e:
            logger.debug("최소 주문 조건 확인 실패(무시): %s", e)

    def _order_params(self, side: Side, is_entry: bool) -> dict:
        if self.exchange_id == "binance":
            # 바이낸스 Hedge Mode: positionSide로 방향을 지정하며, reduceOnly를 함께 보내면
            # "Parameter 'reduceOnly' sent when not required" 오류로 주문이 거부된다.
            return {"positionSide": side.value}
        # Gate.io Dual Mode(및 대부분의 reduceOnly 기반 거래소): 매수/매도 방향이 곧 롱/숏 슬롯을
        # 가리키므로 positionSide가 없고, 청산 주문에만 reduceOnly를 붙여야 반대 포지션이 새로 열리지 않는다.
        return {} if is_entry else {"reduceOnly": True}

    def fill_order(self, side: Side, is_entry: bool, qty: float, price: float) -> None:
        # Gate.io 등 계약(contract) 기반 거래소는 주문 amount가 BTC가 아니라 '계약 수'이며,
        # 계약 1개 = market['contractSize']만큼의 BTC다. 진입 수량은 quantize_qty에서 이미
        # 계약 스텝에 맞게 보정되어 오므로, 여기서는 BTC→계약 변환만 하면 된다(부동소수점
        # 오차로 계약수가 0으로 잘리지 않게 round로 스냅한다).
        market = self.exchange.market(self.symbol)
        contract_size = market.get("contractSize") or 1
        try:
            qty_contracts = float(self.exchange.amount_to_precision(self.symbol, round(qty / contract_size, 8)))
        except Exception:
            qty_contracts = 0.0
        logger.info(
            "주문 실행: BTC수량=%.8f / 계약크기=%s / 계약수=%.4f / 방향=%s / %s / 가격=%.2f",
            qty, contract_size, qty_contracts, side.value, "진입" if is_entry else "청산", price,
        )
        if qty_contracts <= 0:
            raise RuntimeError(
                f"주문 수량이 거래소 최소 단위(1계약={contract_size} BTC) 미만이라 0이 되었습니다 "
                f"(BTC수량={qty:.8f}). 계좌에 실제 USDT 잔고가 있는지 확인하세요."
            )
        self._check_min_notional(qty_contracts, price)
        if side == Side.LONG:
            order_side = "buy" if is_entry else "sell"
        else:
            order_side = "sell" if is_entry else "buy"
        params = self._order_params(side, is_entry)
        self.exchange.create_order(self.symbol, "market", order_side, qty_contracts, None, params)

    def apply_pnl(self, pnl: float) -> None:
        pass  # 실거래 잔고는 거래소가 직접 반영하므로 별도 처리 불필요


def make_qty_provider(broker) -> Callable[[float], float]:
    """1차 진입 규모(USDT) = 레버리지한 잔고(잔고 x 10배)의 2%.

    즉 주문 명목가치(USDT) = 잔고 x LEVERAGE x INITIAL_MARGIN_PCT = 잔고의 20%.
    이 USDT 금액을 현재가로 나눠 BTC 수량으로 바꾼 뒤, broker.quantize_qty로 거래소
    최소 주문 단위에 맞게 보정한다.
    """

    def _provider(price: float) -> float:
        balance = broker.get_balance()
        notional_usdt = balance * LEVERAGE * INITIAL_MARGIN_PCT  # 레버리지한 잔고의 2%
        qty_btc = notional_usdt / price
        logger.info(
            "1차 진입 규모 계산: 잔고=%.4f USDT x 레버리지 %sx x %.0f%% = 주문금액 %.4f USDT (= %.8f BTC)",
            balance, LEVERAGE, INITIAL_MARGIN_PCT * 100, notional_usdt, qty_btc,
        )
        return broker.quantize_qty(qty_btc, price)

    return _provider


# ───────────── 마틴게일 상태머신 (롱/숏 독립 모듈) ─────────────
@dataclass
class Fill:
    price: float
    qty: float


class MartingaleModule:
    """롱 또는 숏 한쪽만 담당하는 완전 독립 상태머신. 반대편 모듈과 상태를 공유하지 않는다."""

    def __init__(self, side: Side, broker, notifier: TelegramNotifier, qty_provider: Callable[[float], float], mode_label: str):
        self.side = side
        self.broker = broker
        self.notifier = notifier
        self.qty_provider = qty_provider
        self.mode_label = mode_label
        self.consecutive_sl = 0
        self.halted = False
        self._reset()

    def _reset(self) -> None:
        self.step = 0
        self.fills: List[Fill] = []
        self.avg_price: Optional[float] = None
        self.total_qty = 0.0
        self.cooldown_until: Optional[float] = None

    def resume(self) -> None:
        """회로차단기로 정지된 모듈을 수동으로 재개한다."""
        self.halted = False
        self.consecutive_sl = 0

    def to_state(self) -> dict:
        return {
            "step": self.step,
            "fills": [{"price": f.price, "qty": f.qty} for f in self.fills],
            "cooldown_until": self.cooldown_until,
            "consecutive_sl": self.consecutive_sl,
            "halted": self.halted,
        }

    def load_state(self, state: dict) -> None:
        self.fills = [Fill(f["price"], f["qty"]) for f in state.get("fills", [])]
        self.step = state.get("step", 0)
        if self.fills:
            self._recalc_avg()
        else:
            self.avg_price = None
            self.total_qty = 0.0
        self.cooldown_until = state.get("cooldown_until")
        self.consecutive_sl = state.get("consecutive_sl", 0)
        self.halted = state.get("halted", False)

    def sync_from_exchange(self, actual_qty: float, entry_price: Optional[float], contract_size: float) -> bool:
        """거래소의 실제 포지션(수량 + 평단가)에 맞춰 내부 상태를 재구성한다.

        저장된 상태와 거래소가 어긋나도(수동 개입/누락 등) 거래소를 '진실'로 삼아 맞춘다.
        평단가/총수량은 거래소 값으로 정확히 맞추고, 물타기 단계(step)는 계약 수로 추정한다.
        반환값: 실제 포지션이 있어 동기화했으면 True, 포지션이 없어 비웠으면 False.
        """
        if not actual_qty or actual_qty <= 0 or not entry_price:
            self._reset()
            return False
        contracts = max(1, round(actual_qty / contract_size)) if contract_size else 1
        # 마틴게일 누적 계약수(1,3,7,15...)에 대응하도록 계약수의 비트길이로 단계를 추정
        step = min(MAX_STEPS, max(1, contracts.bit_length()))
        base = actual_qty / (2 ** step - 1)
        # 같은 평단가로 base, 2base, 4base... 형태의 체결 내역을 재구성(합계 = actual_qty)
        self.fills = [Fill(entry_price, base * (2 ** i)) for i in range(step)]
        self.step = step
        self._recalc_avg()  # avg_price=entry_price, total_qty=actual_qty 로 정리됨
        self.cooldown_until = None
        return True

    @property
    def in_position(self) -> bool:
        return self.step > 0

    def _in_cooldown(self, now: float) -> bool:
        return self.cooldown_until is not None and now < self.cooldown_until

    def _entry_signal(self, price: float, rsi: float, bb: Tuple[float, float, float]) -> bool:
        _, upper, lower = bb
        if self.side == Side.LONG:
            return rsi <= RSI_LONG_TRIGGER or price <= lower
        return rsi >= RSI_SHORT_TRIGGER or price >= upper

    def _pnl_pct(self, price: float) -> float:
        if self.avg_price is None:
            return 0.0
        if self.side == Side.LONG:
            return (price - self.avg_price) / self.avg_price
        return (self.avg_price - price) / self.avg_price

    def _realized_pnl(self, price: float) -> float:
        if self.side == Side.LONG:
            return (price - self.avg_price) * self.total_qty
        return (self.avg_price - price) * self.total_qty

    def on_tick(self, price: float, rsi: Optional[float], bb: Optional[Tuple[float, float, float]], now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        if self.halted or rsi is None or bb is None:
            return

        if not self.in_position:
            if self._in_cooldown(now):
                return
            if self._entry_signal(price, rsi, bb):
                self._enter_initial(price)
            return

        pnl_pct = self._pnl_pct(price)

        if pnl_pct >= TP_PCT:
            self._take_profit(price)
            return

        if pnl_pct <= -STEP_TRIGGER_PCT:
            if self.step < MAX_STEPS:
                self._add_martingale(price)
            else:
                self._stop_loss(price)

    def _recalc_avg(self) -> None:
        self.total_qty = sum(f.qty for f in self.fills)
        self.avg_price = sum(f.price * f.qty for f in self.fills) / self.total_qty

    def _enter_initial(self, price: float) -> None:
        qty = self.qty_provider(price)
        self.broker.fill_order(self.side, True, qty, price)
        self.fills = [Fill(price, qty)]
        self.step = 1
        self._recalc_avg()
        self._notify_entry(price, qty)

    def _add_martingale(self, price: float) -> None:
        qty = self.fills[-1].qty * 2
        self.broker.fill_order(self.side, True, qty, price)
        self.fills.append(Fill(price, qty))
        self.step += 1
        self._recalc_avg()
        self._notify_entry(price, qty)

    def _close_all(self, price: float) -> float:
        pnl = self._realized_pnl(price)
        self.broker.fill_order(self.side, False, self.total_qty, price)
        self.broker.apply_pnl(pnl)
        return pnl

    def _take_profit(self, price: float) -> None:
        pnl = self._close_all(price)
        self._notify_close(price, "익절(TP)", pnl)
        self._reset()
        self.consecutive_sl = 0
        self.cooldown_until = time.time() + COOLDOWN_SEC

    def _stop_loss(self, price: float) -> None:
        pnl = self._close_all(price)
        self._notify_close(price, "하드 손절(SL) → 모듈 리셋", pnl)
        self._reset()
        self.consecutive_sl += 1
        self.cooldown_until = time.time() + COOLDOWN_SEC
        if self.consecutive_sl >= MAX_CONSECUTIVE_SL:
            self.halted = True
            self._notify_halt()

    def _notify_halt(self) -> None:
        self.notifier.send(
            f"[{self.mode_label}] {self.side.value} 자동 정지(회로차단기 작동)\n"
            f"연속 손절 {self.consecutive_sl}회 발생 → 추세 역행 반복 가능성.\n"
            f"수동으로 재시작하거나 resume()을 호출해야 다시 진입합니다."
        )

    def _notify_entry(self, price: float, qty: float) -> None:
        self.notifier.send(
            f"[{self.mode_label}] {self.side.value} {self.step}차 진입\n"
            f"가격: {price:,.2f} / 이번수량: {qty:.6f} / 총수량: {self.total_qty:.6f}\n"
            f"평단가: {self.avg_price:,.2f} / 잔고: {self.broker.get_balance():,.2f}"
        )

    def _notify_close(self, price: float, reason: str, pnl: float) -> None:
        self.notifier.send(
            f"[{self.mode_label}] {self.side.value} {reason}\n"
            f"청산가: {price:,.2f} / 평단가: {self.avg_price:,.2f} / 수량: {self.total_qty:.6f}\n"
            f"실현손익: {pnl:,.2f} / 잔고: {self.broker.get_balance():,.2f}\n"
            f"→ {COOLDOWN_SEC // 60}분 쿨다운 후 재진입 조건 대기"
        )


# ───────────── 봇 엔진 ─────────────
class HedgedMartingaleBot:
    def __init__(self, broker, notifier: TelegramNotifier, mode_label: str, state_path: Optional[str] = None):
        self.broker = broker
        self.notifier = notifier
        self.state_path = state_path
        qty_provider = make_qty_provider(broker)
        self.long = MartingaleModule(Side.LONG, broker, notifier, qty_provider, mode_label)
        self.short = MartingaleModule(Side.SHORT, broker, notifier, qty_provider, mode_label)
        if self.state_path:
            self._load_state()

    def _load_state(self) -> None:
        if not os.path.exists(self.state_path):
            return
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.warning("이전 매매 상태 파일을 읽지 못했습니다(무시하고 처음부터 시작): %s", e)
            return
        self.long.load_state(data.get("long", {}))
        self.short.load_state(data.get("short", {}))
        if self.long.in_position or self.short.in_position:
            logger.info(
                "이전 매매 상태를 복원했습니다 (LONG step=%d avg=%s / SHORT step=%d avg=%s)",
                self.long.step, self.long.avg_price, self.short.step, self.short.avg_price,
            )
        self._sync_with_exchange()

    def _sync_with_exchange(self) -> None:
        """거래소의 실제 포지션을 '진실'로 삼아 내부 상태를 맞춘다(B안: 자동 동기화).

        - 조회 실패(None): 저장된 상태를 그대로 사용
        - 포지션 있음: 거래소의 수량/평단가로 내부 상태를 재구성
        - 포지션 없음: 내부 상태를 비움(거래소가 flat이므로)
        """
        for module in (self.long, self.short):
            pos = self.broker.fetch_position(module.side)
            if pos is None:
                continue  # 조회 불가 → 저장된 상태 유지
            had = module.in_position
            synced = module.sync_from_exchange(pos["qty"], pos.get("entry_price"), pos.get("contract_size") or 1)
            if synced:
                msg = (
                    f"{module.side.value} 거래소 실제 포지션에 맞춰 동기화했습니다 "
                    f"(수량={module.total_qty:.6f}, 평단={module.avg_price:.2f}, step={module.step})."
                )
                logger.info(msg)
                self.notifier.send(f"[{module.mode_label}] {msg}")
            elif had:
                logger.info("%s 거래소에 실제 포지션이 없어 내부 상태를 초기화했습니다.", module.side.value)

    def _save_state(self) -> None:
        if not self.state_path:
            return
        try:
            with open(self.state_path, "w", encoding="utf-8") as f:
                json.dump({"long": self.long.to_state(), "short": self.short.to_state()}, f)
        except Exception as e:
            logger.warning("매매 상태 저장 실패: %s", e)

    def on_price(self, price: float, closes_window: List[float], now: Optional[float] = None) -> None:
        rsi = Indicators.rsi(closes_window)
        bb = Indicators.bollinger(closes_window)
        self.long.on_tick(price, rsi, bb, now)
        self.short.on_tick(price, rsi, bb, now)
        self._save_state()

    def run_forever(self, market_data: PublicMarketData, poll_sec: int = POLL_SEC, stop_event: Optional[threading.Event] = None) -> None:
        logger.info("프로그램 버전: %s", VERSION)
        logger.info("자동매매 시작 (%s, 레버리지 %sx, %s)", self.long.mode_label, LEVERAGE, TIMEFRAME)
        try:
            logger.info("현재 계좌 주문가능 잔고: %.4f USDT", self.broker.get_balance())
        except Exception as e:
            logger.warning("잔고 조회 실패: %s", e)
        while stop_event is None or not stop_event.is_set():
            try:
                closes = market_data.get_closes()
                self.on_price(closes[-1], closes)
            except Exception as e:
                logger.warning("루프 오류: %s", e)
            if stop_event is not None:
                if stop_event.wait(poll_sec):
                    break
            else:
                time.sleep(poll_sec)
        logger.info("자동매매 루프가 정지되었습니다.")


# ───────────── 가상 차트 데이터 정성 테스트 ─────────────
def generate_synthetic_prices() -> List[float]:
    """롱/숏 1차 진입 조건이 각각 최소 한 번씩 발생하도록 설계한 가상 15분봉 종가 시퀀스."""
    random.seed(7)
    prices = [50_000.0]

    def add_walk(n: int, drift: float, noise: float) -> None:
        for _ in range(n):
            change = drift + random.uniform(-noise, noise)
            prices.append(prices[-1] * (1 + change))

    add_walk(25, 0.0, 0.0015)     # 초반 횡보(지표 워밍업)
    add_walk(20, -0.010, 0.002)   # 급락 구간 → RSI<=40 / 하단밴드 터치 유도
    add_walk(15, 0.0, 0.0015)     # 중간 횡보(재정비 + 3분 쿨다운 경과)
    add_walk(20, 0.010, 0.002)    # 급등 구간 → RSI>=60 / 상단밴드 터치 유도
    add_walk(15, 0.0, 0.0015)     # 마무리 횡보
    return prices


def run_self_test() -> None:
    print("=" * 60)
    print("가상 차트 데이터로 롱/숏 1차 진입 조건 정성 테스트")
    print("=" * 60)

    notifier = TelegramNotifier(token="", chat_id="")  # 콘솔 로그로만 출력(텔레그램 미설정)
    broker = PaperBroker(starting_balance=PAPER_START_BALANCE)
    bot = HedgedMartingaleBot(broker, notifier, mode_label="PAPER-SELFTEST")

    prices = generate_synthetic_prices()
    sim_time = time.time()
    long_triggered = False
    short_triggered = False

    for i in range(BB_PERIOD, len(prices)):
        window = prices[: i + 1]
        price = window[-1]
        rsi_val = Indicators.rsi(window)

        was_long_in_pos = bot.long.in_position
        was_short_in_pos = bot.short.in_position

        bot.on_price(price, window, now=sim_time)

        if not was_long_in_pos and bot.long.in_position:
            long_triggered = True
            print(f"[bar {i:3d}] LONG 1차 진입  price={price:,.2f}  RSI={rsi_val:.1f}")
        if not was_short_in_pos and bot.short.in_position:
            short_triggered = True
            print(f"[bar {i:3d}] SHORT 1차 진입  price={price:,.2f}  RSI={rsi_val:.1f}")

        sim_time += 900  # 15분봉 1개 = 900초

    print("-" * 60)
    print(f"LONG  1차 진입 트리거: {'성공' if long_triggered else '실패'}")
    print(f"SHORT 1차 진입 트리거: {'성공' if short_triggered else '실패'}")
    print(f"최종 모의 잔고: {broker.get_balance():,.2f} USDT")
    print("=" * 60)


def run_telegram_test() -> None:
    """TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 환경변수로 실제 발송이 되는지 확인한다."""
    print("=" * 60)
    print("텔레그램 알림 발송 테스트")
    print("=" * 60)
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 환경변수가 설정되지 않았습니다.")
        print("예) export TELEGRAM_BOT_TOKEN=... / export TELEGRAM_CHAT_ID=...")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    text = "[hedged_martingale_bot] 텔레그램 알림 발송 테스트입니다. 이 메시지가 보이면 정상 연결된 것입니다."
    try:
        resp = requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=10)
        body = resp.json()
    except Exception as e:
        print(f"전송 요청 자체가 실패했습니다(네트워크/토큰 확인): {e}")
        return
    if resp.status_code == 200 and body.get("ok"):
        print("전송 성공! 텔레그램 앱에서 메시지를 확인하세요.")
    else:
        print(f"전송 실패 (HTTP {resp.status_code}): {body}")
        print("chat_id가 정확한지, 봇에게 먼저 메시지를 보냈는지 확인하세요.")
    print("=" * 60)


# ───────────── 실행 진입점 ─────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="비트코인 10배 레버리지 양방향 마틴게일 자동매매 봇")
    parser.add_argument("--selftest", action="store_true", help="가상 차트 데이터로 진입 조건 정성 테스트만 실행")
    parser.add_argument("--telegram-test", action="store_true", help="텔레그램 알림이 실제로 오는지 테스트 발송만 하고 종료")
    parser.add_argument("--live", action="store_true", help="실거래 모드로 실행 (기본값: 모의매매)")
    parser.add_argument("--poll-sec", type=int, default=POLL_SEC, help="가격 조회 주기(초)")
    args = parser.parse_args()

    if args.selftest:
        run_self_test()
        return

    if args.telegram_test:
        run_telegram_test()
        return

    notifier = TelegramNotifier()

    if args.live:
        api_key = os.environ.get("EXCHANGE_API_KEY", "")
        api_secret = os.environ.get("EXCHANGE_API_SECRET", "")
        if not api_key or not api_secret:
            raise SystemExit("실거래 모드는 EXCHANGE_API_KEY / EXCHANGE_API_SECRET 환경변수가 필요합니다.")
        broker = LiveBroker(EXCHANGE_ID, api_key, api_secret)
        mode_label = "LIVE"
    else:
        broker = PaperBroker()
        mode_label = "PAPER"

    bot = HedgedMartingaleBot(broker, notifier, mode_label, state_path=STATE_PATH)
    market_data = PublicMarketData()
    logger.info("거래소: %s / 심볼: %s / 상태 저장 위치: %s", EXCHANGE_ID, SYMBOL, STATE_PATH)
    bot.run_forever(market_data, poll_sec=args.poll_sec)


if __name__ == "__main__":
    main()
