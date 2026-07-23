"""
hedged_martingale_bot.py
비트코인 10배 레버리지 양방향(Hedge Mode) 마틴게일 자동매매 모듈

- 기본 실행 모드: 모의 매매(Paper Trading). 실거래는 --live 옵션 + API 키가 있을 때만 동작.
- 롱/숏은 완전히 독립된 상태머신(MartingaleModule)이며 서로의 진입/청산에 영향을 주지 않는다.
- 시세는 바이낸스 선물 공개 REST API(인증 불필요)에서 15분봉 종가를 가져온다.
- 실거래 시에만 ccxt로 Hedge Mode(양방향 포지션) + 레버리지 10배를 계좌에 설정한다.

사용법:
    python hedged_martingale_bot.py --selftest      # 가상 차트로 진입 조건 정성 테스트
    python hedged_martingale_bot.py                 # 모의 매매로 실시간 루프 실행
    python hedged_martingale_bot.py --live           # 실거래 (EXCHANGE_API_KEY/SECRET 환경변수 필요)
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import random
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, List, Optional, Tuple

import requests

try:
    import ccxt
except ImportError:
    ccxt = None


# ───────────── 전략 설정값 ─────────────
SYMBOL = os.environ.get("SYMBOL", "BTC/USDT:USDT")
BINANCE_PUBLIC_SYMBOL = os.environ.get("BINANCE_PUBLIC_SYMBOL", "BTCUSDT")
TIMEFRAME = "15m"
LEVERAGE = 10
INITIAL_MARGIN_PCT = 0.02       # 1차 진입 마진 = 계좌 잔고의 2%
STEP_TRIGGER_PCT = 0.003        # 평단가 대비 0.3% 역방향 이동 시 물타기/손절 트리거
TP_PCT = 0.003                  # 평단가 대비 0.3% 순방향 이동 시 익절
MAX_STEPS = 4                   # 1배 -> 2배 -> 4배 -> 8배
COOLDOWN_SEC = 180              # 청산 후 재진입 대기 3분
RSI_PERIOD = 14
RSI_LONG_TRIGGER = 40.0
RSI_SHORT_TRIGGER = 60.0
BB_PERIOD = 20
BB_STDDEV_MULT = 2.0
PAPER_START_BALANCE = float(os.environ.get("PAPER_START_BALANCE", "10000"))

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
            return  # 토큰/채팅ID 미설정 시 콘솔 로그로만 대체
        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            requests.post(url, data={"chat_id": self.chat_id, "text": text}, timeout=5)
        except Exception as e:
            logger.warning("텔레그램 전송 실패: %s", e)


# ───────────── 시세 데이터 (공개 API, 인증 불필요) ─────────────
class BinancePublicMarketData:
    def __init__(self, symbol: str = BINANCE_PUBLIC_SYMBOL, timeframe: str = TIMEFRAME, limit: int = 100):
        self.symbol = symbol
        self.timeframe = timeframe
        self.limit = limit

    def get_closes(self) -> List[float]:
        url = "https://fapi.binance.com/fapi/v1/klines"
        params = {"symbol": self.symbol, "interval": self.timeframe, "limit": self.limit}
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        return [float(row[4]) for row in resp.json()]


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

    def fill_order(self, side: Side, is_entry: bool, qty: float, price: float) -> None:
        logger.info(
            "[모의체결] %s %s qty=%.6f price=%.2f",
            side.value, "진입" if is_entry else "청산", qty, price,
        )

    def apply_pnl(self, pnl: float) -> None:
        self.balance += pnl


class LiveBroker:
    """실거래 브로커. 최초 연결 시 계좌에 Hedge Mode(양방향)와 레버리지 10배를 설정한다."""

    def __init__(self, exchange_id: str, api_key: str, api_secret: str, symbol: str = SYMBOL, leverage: int = LEVERAGE):
        if ccxt is None:
            raise RuntimeError("ccxt가 설치되어 있지 않습니다. 실거래 모드는 'pip install ccxt'가 필요합니다.")
        exchange_cls = getattr(ccxt, exchange_id)
        self.exchange = exchange_cls({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "future"},
        })
        self.symbol = symbol
        self._setup_account(leverage)

    def _setup_account(self, leverage: int) -> None:
        try:
            self.exchange.set_position_mode(hedged=True)
        except Exception as e:
            logger.warning("Hedge Mode 설정 실패(이미 설정돼 있을 수 있음): %s", e)
        try:
            self.exchange.set_leverage(leverage, self.symbol)
        except Exception as e:
            logger.warning("레버리지 설정 실패: %s", e)

    def get_balance(self) -> float:
        bal = self.exchange.fetch_balance()
        return float(bal.get("USDT", {}).get("free", 0.0))

    def fill_order(self, side: Side, is_entry: bool, qty: float, price: float) -> None:
        if side == Side.LONG:
            order_side = "buy" if is_entry else "sell"
        else:
            order_side = "sell" if is_entry else "buy"
        params = {"positionSide": side.value}
        if not is_entry:
            params["reduceOnly"] = True
        self.exchange.create_order(self.symbol, "market", order_side, qty, None, params)

    def apply_pnl(self, pnl: float) -> None:
        pass  # 실거래 잔고는 거래소가 직접 반영하므로 별도 처리 불필요


def make_qty_provider(broker) -> Callable[[float], float]:
    """1차 진입 수량 = (잔고 * 2% 마진) * 10배 레버리지 / 현재가."""

    def _provider(price: float) -> float:
        margin = broker.get_balance() * INITIAL_MARGIN_PCT
        notional = margin * LEVERAGE
        return notional / price

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
        self._reset()

    def _reset(self) -> None:
        self.step = 0
        self.fills: List[Fill] = []
        self.avg_price: Optional[float] = None
        self.total_qty = 0.0
        self.cooldown_until: Optional[float] = None

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
        if rsi is None or bb is None:
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
        self.cooldown_until = time.time() + COOLDOWN_SEC

    def _stop_loss(self, price: float) -> None:
        pnl = self._close_all(price)
        self._notify_close(price, "하드 손절(SL) → 모듈 리셋", pnl)
        self._reset()
        self.cooldown_until = time.time() + COOLDOWN_SEC

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
    def __init__(self, broker, notifier: TelegramNotifier, mode_label: str):
        self.broker = broker
        self.notifier = notifier
        qty_provider = make_qty_provider(broker)
        self.long = MartingaleModule(Side.LONG, broker, notifier, qty_provider, mode_label)
        self.short = MartingaleModule(Side.SHORT, broker, notifier, qty_provider, mode_label)

    def on_price(self, price: float, closes_window: List[float], now: Optional[float] = None) -> None:
        rsi = Indicators.rsi(closes_window)
        bb = Indicators.bollinger(closes_window)
        self.long.on_tick(price, rsi, bb, now)
        self.short.on_tick(price, rsi, bb, now)

    def run_forever(self, market_data: BinancePublicMarketData, poll_sec: int = 30) -> None:
        logger.info("자동매매 시작 (%s, 레버리지 %sx, %s)", self.long.mode_label, LEVERAGE, TIMEFRAME)
        while True:
            try:
                closes = market_data.get_closes()
                self.on_price(closes[-1], closes)
            except Exception as e:
                logger.warning("루프 오류: %s", e)
            time.sleep(poll_sec)


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


# ───────────── 실행 진입점 ─────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="비트코인 10배 레버리지 양방향 마틴게일 자동매매 봇")
    parser.add_argument("--selftest", action="store_true", help="가상 차트 데이터로 진입 조건 정성 테스트만 실행")
    parser.add_argument("--live", action="store_true", help="실거래 모드로 실행 (기본값: 모의매매)")
    parser.add_argument("--poll-sec", type=int, default=30, help="가격 조회 주기(초)")
    args = parser.parse_args()

    if args.selftest:
        run_self_test()
        return

    notifier = TelegramNotifier()

    if args.live:
        api_key = os.environ.get("EXCHANGE_API_KEY", "")
        api_secret = os.environ.get("EXCHANGE_API_SECRET", "")
        exchange_id = os.environ.get("EXCHANGE_ID", "binance")
        if not api_key or not api_secret:
            raise SystemExit("실거래 모드는 EXCHANGE_API_KEY / EXCHANGE_API_SECRET 환경변수가 필요합니다.")
        broker = LiveBroker(exchange_id, api_key, api_secret)
        mode_label = "LIVE"
    else:
        broker = PaperBroker()
        mode_label = "PAPER"

    bot = HedgedMartingaleBot(broker, notifier, mode_label)
    market_data = BinancePublicMarketData()
    bot.run_forever(market_data, poll_sec=args.poll_sec)


if __name__ == "__main__":
    main()
