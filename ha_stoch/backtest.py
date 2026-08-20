"""
ha_stoch/backtest.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
하이킨 애쉬 + EMA200 + 스토캐스틱 RSI 전략의 신호 생성과 백테스트.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
전략 규칙
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ① 추세 필터   : 종가가 EMA200 위 → 롱만, 아래 → 숏만
  ② 되돌림 타이밍: 스토캐스틱 RSI의 K가 D를 과매도 구간에서 상향 교차
                   (숏은 과매수 구간에서 하향 교차)
  ③ 진입 확인   : 직전 캔들보다 몸통이 길고, 아래 꼬리가 없는
                   양봉 하이킨 애쉬 캔들 (숏은 위 꼬리 없는 음봉)
  ④ 손익비      : 익절 = 손절폭 × 2 (R:R 2:1)

②가 나온 뒤 ③을 몇 봉까지 기다릴지는 signal_valid_bars로 정합니다.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ 체결가 규칙 — 이 파일에서 가장 중요한 부분
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
하이킨 애쉬 값은 '신호 판정'에만 씁니다.
진입가·손절가·익절가는 전부 원본 open/high/low/close로 계산합니다.

HA 종가는 시장에 존재한 적 없는 평균값이라, 거기에 체결시키면
실제로는 낼 수 없는 주문이 체결된 것으로 계산되어 수익률이 부풀려집니다.

또한 신호는 봉이 '마감돼야' 확정되므로, 진입은 항상 다음 봉 시가입니다.
같은 봉 종가에 진입시키면 미래참조가 됩니다.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from .indicators import heikin_ashi, ema, stoch_rsi, atr


@dataclass
class HAStochConfig:
    # ─── 시장 ───
    exchange: str = "okx"
    symbol: str = "BTC/USDT:USDT"
    market_type: str = "future"
    timeframe: str = "1h"

    # ─── 지표 ───
    ema_period: int = 200
    rsi_period: int = 14
    stoch_period: int = 14
    k_smooth: int = 3
    d_smooth: int = 3
    oversold: float = 20.0        # 이 아래에서 상향 교차해야 롱 신호
    overbought: float = 80.0      # 이 위에서 하향 교차해야 숏 신호

    # ─── 신호 ───
    signal_valid_bars: int = 5    # 교차 후 확인 캔들을 기다리는 최대 봉 수
    require_no_wick: bool = True  # 아래(위) 꼬리 없음 조건을 요구할지
    require_bigger_body: bool = True
    trend_on_ha: bool = False     # EMA200을 HA 종가로 볼지 (기본: 원본 종가)

    # ─── 진입/청산 ───
    reward_risk: float = 2.0      # 익절 = 손절폭 × 이 값
    sl_mode: str = "atr"          # 'atr' | 'swing' | 'candle'
    sl_atr_mult: float = 1.5
    sl_swing_lookback: int = 10
    atr_period: int = 14

    # ─── 자금 관리 ───
    initial_capital: float = 10_000.0
    risk_pct: float = 1.0
    max_leverage: float = 10.0
    allow_long: bool = True
    allow_short: bool = True

    # ─── 체결 비용 (ICT 전략과 동일하게 맞춰 비교 가능하게 함) ───
    maker_fee: float = 0.0002
    taker_fee: float = 0.0005
    slippage_bps: float = 1.0

    warmup_bars: int = 250

    def validate(self) -> "HAStochConfig":
        if self.sl_mode not in ("atr", "swing", "candle"):
            raise ValueError(f"sl_mode는 'atr'/'swing'/'candle' 중 하나여야 합니다: {self.sl_mode!r}")
        if self.reward_risk <= 0:
            raise ValueError("reward_risk는 0보다 커야 합니다.")
        if not 0 < self.risk_pct <= 100:
            raise ValueError("risk_pct는 0 초과 100 이하여야 합니다.")
        if not self.allow_long and not self.allow_short:
            raise ValueError("allow_long과 allow_short를 모두 끄면 거래가 없습니다.")
        if self.oversold >= self.overbought:
            raise ValueError("oversold는 overbought보다 작아야 합니다.")
        if self.warmup_bars < self.ema_period:
            raise ValueError(
                f"warmup_bars({self.warmup_bars})가 ema_period({self.ema_period})보다 작으면 "
                f"EMA가 덜 익은 구간에서 거래하게 됩니다."
            )
        return self

    def to_dict(self) -> dict:
        return asdict(self)


def build_signals(df: pd.DataFrame, cfg: HAStochConfig) -> dict:
    """봉마다 신호 판정에 필요한 값들을 미리 계산한다.

    반환되는 배열은 전부 '그 봉이 마감된 시점에 알 수 있는 값'이다.
    """
    ha = heikin_ashi(df)
    ha_open = ha["ha_open"].to_numpy(dtype=float)
    ha_close = ha["ha_close"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    n = len(df)

    trend_source = ha["ha_close"] if cfg.trend_on_ha else df["close"]
    ema_line = ema(trend_source, cfg.ema_period)
    k, d = stoch_rsi(df["close"], cfg.rsi_period, cfg.stoch_period,
                     cfg.k_smooth, cfg.d_smooth)

    # ── 추세: 종가가 EMA200 위/아래 ──
    close = df["close"].to_numpy(dtype=float)
    with np.errstate(invalid="ignore"):
        above_ema = close > ema_line
        below_ema = close < ema_line

    # ── 스토캐스틱 RSI 교차 ──
    k_prev = np.roll(k, 1)
    d_prev = np.roll(d, 1)
    k_prev[0] = d_prev[0] = np.nan

    with np.errstate(invalid="ignore"):
        # 과매도 구간에서 K가 D를 상향 돌파
        cross_up = (k_prev <= d_prev) & (k > d) & (k_prev < cfg.oversold)
        # 과매수 구간에서 K가 D를 하향 돌파
        cross_down = (k_prev >= d_prev) & (k < d) & (k_prev > cfg.overbought)
    cross_up = np.nan_to_num(cross_up, nan=False).astype(bool)
    cross_down = np.nan_to_num(cross_down, nan=False).astype(bool)

    # ── 하이킨 애쉬 확인 캔들 ──
    body = np.abs(ha_close - ha_open)
    body_prev = np.roll(body, 1)
    body_prev[0] = np.nan

    bull_candle = ha_close > ha_open
    bear_candle = ha_close < ha_open

    # 양봉 HA에서 아래 꼬리 없음 ⟺ 원본 저가가 HA시가 아래로 내려가지 않음
    # (HA저가 = min(저가, HA시가, HA종가) 이고 양봉이면 HA종가 > HA시가이므로)
    no_lower_wick = low >= ha_open
    no_upper_wick = high <= ha_open

    with np.errstate(invalid="ignore"):
        bigger_body = body > body_prev

    if not cfg.require_bigger_body:
        bigger_body = np.ones(n, dtype=bool)
    if not cfg.require_no_wick:
        no_lower_wick = np.ones(n, dtype=bool)
        no_upper_wick = np.ones(n, dtype=bool)

    confirm_long = bull_candle & np.nan_to_num(bigger_body, nan=False).astype(bool) & no_lower_wick
    confirm_short = bear_candle & np.nan_to_num(bigger_body, nan=False).astype(bool) & no_upper_wick

    return {
        "ema": ema_line,
        "k": k, "d": d,
        "above_ema": above_ema, "below_ema": below_ema,
        "cross_up": cross_up, "cross_down": cross_down,
        "confirm_long": confirm_long, "confirm_short": confirm_short,
        "atr": atr(df, cfg.atr_period),
        "ha_open": ha_open, "ha_close": ha_close,
    }


def _stop_price(direction: int, i: int, df_arrays: dict, sig: dict, cfg: HAStochConfig) -> float:
    """손절가를 계산한다. 전부 '원본' 가격 기준이다."""
    low, high = df_arrays["low"], df_arrays["high"]
    entry_ref = df_arrays["close"][i]
    atr_value = sig["atr"][i]

    if cfg.sl_mode == "atr":
        if not np.isfinite(atr_value):
            return np.nan
        offset = cfg.sl_atr_mult * atr_value
        return entry_ref - offset if direction > 0 else entry_ref + offset

    if cfg.sl_mode == "swing":
        start = max(0, i - cfg.sl_swing_lookback + 1)
        return float(low[start:i + 1].min()) if direction > 0 else float(high[start:i + 1].max())

    # 'candle' — 신호 캔들의 저가/고가 바깥
    return float(low[i]) if direction > 0 else float(high[i])


def run_backtest(df: pd.DataFrame, cfg: HAStochConfig):
    """봉을 하나씩 순회하며 백테스트한다.

    회계 규칙은 ICT 전략 엔진과 동일하게 맞췄다 (비교 가능하도록):
      · 한 봉에서 손절/익절에 모두 닿으면 항상 손절 먼저
      · 손절은 시장가 수수료 + 슬리피지, 익절은 지정가 수수료
      · 진입은 지정가가 아니라 '다음 봉 시가' 시장가이므로 taker 수수료
      · 리스크 비율 기반 수량 산정, 명목가치 레버리지 상한
    """
    from ict_fvg.backtest import compute_metrics, BacktestResult
    from ict_fvg.config import StrategyConfig

    cfg.validate()
    sig = build_signals(df, cfg)

    arrays = {
        "open": df["open"].to_numpy(dtype=float),
        "high": df["high"].to_numpy(dtype=float),
        "low": df["low"].to_numpy(dtype=float),
        "close": df["close"].to_numpy(dtype=float),
    }
    open_, high, low, close = arrays["open"], arrays["high"], arrays["low"], arrays["close"]
    index = df.index
    n = len(df)
    slip = cfg.slippage_bps / 10_000.0

    equity = cfg.initial_capital
    equity_curve = np.full(n, equity, dtype=float)
    trades = []
    position = None

    # 교차가 일어난 뒤 확인 캔들을 기다리는 상태
    long_armed_at = short_armed_at = -1
    rejections = {"손절_무효": 0, "수량_0": 0, "확인캔들_시간초과": 0}

    for i in range(n):
        # ── 1) 보유 포지션의 손절/익절 확인 ──
        if position is not None:
            exit_info = _check_exit(position, high[i], low[i], slip, cfg)
            if exit_info is not None:
                equity = _close_position(position, exit_info, index[i], i, equity, trades)
                position = None

        # ── 2) 교차 발생 시 대기 상태로 전환 ──
        if sig["cross_up"][i] and sig["above_ema"][i]:
            long_armed_at = i
            short_armed_at = -1
        if sig["cross_down"][i] and sig["below_ema"][i]:
            short_armed_at = i
            long_armed_at = -1

        # 대기 시간 초과 처리
        if long_armed_at >= 0 and i - long_armed_at > cfg.signal_valid_bars:
            long_armed_at = -1
            rejections["확인캔들_시간초과"] += 1
        if short_armed_at >= 0 and i - short_armed_at > cfg.signal_valid_bars:
            short_armed_at = -1
            rejections["확인캔들_시간초과"] += 1

        # ── 3) 확인 캔들이 나왔는지 판정 ──
        #    ⚠️ 이 봉은 지금 막 마감됐으므로, 진입은 다음 봉(i+1) 시가다.
        if position is None and i >= cfg.warmup_bars and i + 1 < n:
            direction = 0
            if (cfg.allow_long and long_armed_at >= 0
                    and sig["confirm_long"][i] and sig["above_ema"][i]):
                direction = 1
            elif (cfg.allow_short and short_armed_at >= 0
                  and sig["confirm_short"][i] and sig["below_ema"][i]):
                direction = -1

            if direction != 0:
                stop = _stop_price(direction, i, arrays, sig, cfg)
                entry = open_[i + 1]  # 다음 봉 시가에 시장가 진입
                entry *= (1 + slip) if direction > 0 else (1 - slip)

                valid = (np.isfinite(stop)
                         and ((direction > 0 and stop < entry) or (direction < 0 and stop > entry)))
                if not valid:
                    rejections["손절_무효"] += 1
                else:
                    risk = abs(entry - stop)
                    target = entry + direction * cfg.reward_risk * risk
                    qty = _position_size(equity, entry, stop, cfg)
                    if qty <= 0:
                        rejections["수량_0"] += 1
                    else:
                        position = {
                            "direction": direction,
                            "entry_time": index[i + 1],
                            "entry_idx": i + 1,
                            "entry_price": entry,
                            "stop_price": stop,
                            "target_price": target,
                            "qty": qty,
                            "entry_fee": entry * qty * cfg.taker_fee,
                            "planned_rr": cfg.reward_risk,
                        }
                        long_armed_at = short_armed_at = -1

        # ── 4) 평가 자산 ──
        if position is not None and i >= position["entry_idx"]:
            unrealised = (close[i] - position["entry_price"]) * position["qty"] * position["direction"]
            equity_curve[i] = equity + unrealised
        else:
            equity_curve[i] = equity

    if position is not None:
        exit_info = {"price": close[n - 1], "reason": "종료시_청산", "fee_rate": cfg.taker_fee}
        equity = _close_position(position, exit_info, index[n - 1], n - 1, equity, trades)
        equity_curve[n - 1] = equity

    trades_df = _trades_to_frame(trades)
    curve = pd.Series(equity_curve, index=index, name="equity")

    # 지표 계산은 ICT 전략과 같은 함수를 써서 수치를 그대로 비교할 수 있게 한다
    metrics_cfg = StrategyConfig(initial_capital=cfg.initial_capital, ltf=cfg.timeframe)
    metrics = compute_metrics(trades_df, curve, metrics_cfg)

    return BacktestResult(trades=trades_df, equity_curve=curve, metrics=metrics,
                          config=cfg.to_dict(), rejections=rejections)


def _check_exit(position, bar_high, bar_low, slip, cfg):
    """한 봉에서 손절/익절 도달 여부. 둘 다 닿으면 손절 우선(보수적)."""
    if position["direction"] > 0:
        if bar_low <= position["stop_price"]:
            return {"price": position["stop_price"] * (1 - slip), "reason": "손절",
                    "fee_rate": cfg.taker_fee}
        if bar_high >= position["target_price"]:
            return {"price": position["target_price"], "reason": "익절",
                    "fee_rate": cfg.maker_fee}
        return None

    if bar_high >= position["stop_price"]:
        return {"price": position["stop_price"] * (1 + slip), "reason": "손절",
                "fee_rate": cfg.taker_fee}
    if bar_low <= position["target_price"]:
        return {"price": position["target_price"], "reason": "익절",
                "fee_rate": cfg.maker_fee}
    return None


def _position_size(equity, entry, stop, cfg):
    distance = abs(entry - stop)
    if distance <= 0 or not np.isfinite(distance):
        return 0.0
    qty = equity * (cfg.risk_pct / 100.0) / distance
    max_notional = equity * cfg.max_leverage
    if qty * entry > max_notional:
        qty = max_notional / entry
    return qty if np.isfinite(qty) and qty > 0 else 0.0


def _close_position(position, exit_info, exit_time, exit_idx, equity, trades):
    from ict_fvg.backtest import Trade

    exit_price = exit_info["price"]
    qty = position["qty"]
    gross = (exit_price - position["entry_price"]) * qty * position["direction"]
    fees = position["entry_fee"] + exit_price * qty * exit_info["fee_rate"]
    net = gross - fees

    risk = abs(position["entry_price"] - position["stop_price"])
    equity += net

    trades.append(Trade(
        direction="LONG" if position["direction"] > 0 else "SHORT",
        entry_time=position["entry_time"],
        entry_price=position["entry_price"],
        stop_price=position["stop_price"],
        target_price=position["target_price"],
        qty=qty,
        exit_time=exit_time,
        exit_price=exit_price,
        exit_reason=exit_info["reason"],
        gross_pnl=gross,
        fees=fees,
        net_pnl=net,
        r_multiple=net / (risk * qty) if risk > 0 and qty > 0 else 0.0,
        planned_rr=position["planned_rr"],
        bars_held=exit_idx - position["entry_idx"],
        equity_after=equity,
    ))
    return equity


def _trades_to_frame(trades) -> pd.DataFrame:
    columns = ["direction", "entry_time", "entry_price", "stop_price", "target_price", "qty",
               "exit_time", "exit_price", "exit_reason", "gross_pnl", "fees", "net_pnl",
               "r_multiple", "planned_rr", "bars_held", "equity_after"]
    if not trades:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame([t.__dict__ for t in trades])[columns]
