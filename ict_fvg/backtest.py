"""
ict_fvg/backtest.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ICT FVG 전략의 백테스트 엔진과 성과 지표.

봉을 하나씩 앞에서부터 순회하면서, 그 시점에 "실제로 알 수 있었던 정보"만 가지고
진입/청산을 판단합니다. 미래 데이터는 절대 쓰지 않습니다.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ 백테스트 결과를 볼 때 반드시 알아야 할 한계
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. 봉 안에서 가격이 어떤 순서로 움직였는지는 OHLC만으로 알 수 없습니다.
   한 봉에서 손절가와 익절가에 모두 닿았다면, 이 엔진은 항상 '손절 먼저'로 계산합니다.
   (실제보다 나쁘게 잡는 보수적 가정 — 낙관적으로 잡으면 결과가 거짓으로 좋아집니다)

2. 진입은 CE에 걸어둔 지정가로 가정합니다. 실제로는 가격이 CE를 스쳤는데도
   내 주문이 체결되지 않을 수 있습니다(호가 대기열). 이 부분은 백테스트가 유리합니다.

3. 자금조달 수수료(Funding Fee), 부분 체결, 거래소 장애는 반영하지 않습니다.

즉 여기 나온 수익률은 상한선에 가깝습니다. 실전은 이보다 나쁩니다.
반드시 소액 또는 데모 계정에서 먼저 검증하세요.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import timeframe_ms
from .fvg import (
    BULLISH, BEARISH, detect_fvgs,
    passes_size_filter, in_discount_zone, in_premium_zone,
)
from .structure import (
    build_ltf_structure, last_confirmed_ltf_swings, align_htf_range, compute_atr,
)


@dataclass
class Trade:
    direction: str
    entry_time: pd.Timestamp
    entry_price: float
    stop_price: float
    target_price: float
    qty: float
    exit_time: pd.Timestamp = None
    exit_price: float = None
    exit_reason: str = None
    gross_pnl: float = 0.0
    fees: float = 0.0
    net_pnl: float = 0.0
    r_multiple: float = 0.0
    planned_rr: float = 0.0
    bars_held: int = 0
    equity_after: float = 0.0
    fvg_top: float = 0.0
    fvg_bottom: float = 0.0
    fvg_ce: float = 0.0


@dataclass
class BacktestResult:
    trades: pd.DataFrame
    equity_curve: pd.Series
    metrics: dict
    config: dict
    rejections: dict = field(default_factory=dict)

    def summary(self) -> str:
        return format_report(self)


# ══════════════════════════════════════════════════════════════════
#  진입 조건 판정 헬퍼
# ══════════════════════════════════════════════════════════════════

def _setup_active(stage: int, cfg) -> bool:
    """유동성 사냥 / 구조 전환 조건이 충족됐는지.

    require_sweep=False면 필터를 끄는 것이므로 항상 True.
    require_mss=True면 MSS(2단계)까지 요구하고, False면 사냥(1단계)만으로 충분.
    """
    if not cfg.require_sweep:
        return True
    return stage >= (2 if cfg.require_mss else 1)


def _pick_target(direction: int, entry: float, ctx: dict, i: int, cfg) -> float:
    """익절 목표가를 고른다.

    'nearest'    → 진입가 너머에서 가장 가까운 '확정된' HTF 스윙
    'range_high' → 현재 딜링 레인지의 반대편 끝

    ⚠️ sh_count[i]는 봉 i 시점까지 확정된 스윙 개수다. 이 슬라이싱이
       미래의 스윙을 목표로 삼는 것을 막아준다.
    """
    if direction == BULLISH:
        if cfg.tp_target == "range_high":
            level = ctx["range_high"][i]
            return level if np.isfinite(level) and level > entry else np.nan
        available = ctx["sh_levels"][: ctx["sh_count"][i]]
        above = available[available > entry]
        return float(above.min()) if above.size else np.nan

    if cfg.tp_target == "range_high":
        level = ctx["range_low"][i]
        return level if np.isfinite(level) and level < entry else np.nan
    available = ctx["sl_levels"][: ctx["sl_count"][i]]
    below = available[available < entry]
    return float(below.max()) if below.size else np.nan


def _pick_stop(direction: int, fvg, ltf_swing_high, ltf_swing_low, i: int,
               atr_value: float, cfg) -> float:
    """손절가를 고른다.

    'fvg'   → 갭 반대편 끝
    'swing' → 직전 LTF 스윙 바깥
    'wider' → 둘 중 더 먼 쪽 (기본값, 가장 보수적)

    마지막에 ATR 기준 여유를 더해 스탑 헌팅에 조금 덜 걸리게 한다.
    """
    buffer = cfg.sl_buffer_atr * atr_value if np.isfinite(atr_value) else 0.0

    if direction == BULLISH:
        candidates = []
        if cfg.sl_mode in ("fvg", "wider"):
            candidates.append(fvg.bottom)
        if cfg.sl_mode in ("swing", "wider"):
            swing = ltf_swing_low[i]
            if np.isfinite(swing):
                candidates.append(swing)
        if not candidates:
            candidates.append(fvg.bottom)
        return min(candidates) - buffer

    candidates = []
    if cfg.sl_mode in ("fvg", "wider"):
        candidates.append(fvg.top)
    if cfg.sl_mode in ("swing", "wider"):
        swing = ltf_swing_high[i]
        if np.isfinite(swing):
            candidates.append(swing)
    if not candidates:
        candidates.append(fvg.top)
    return max(candidates) + buffer


def _position_size(equity: float, entry: float, stop: float, cfg) -> float:
    """1회 거래당 잃을 금액을 고정하고, 손절까지의 거리로 수량을 역산한다.

    손절이 가까우면 수량이 커지므로, 명목가치가 자본의 max_leverage배를
    넘지 않도록 상한을 둔다.
    """
    stop_distance = abs(entry - stop)
    if stop_distance <= 0 or not np.isfinite(stop_distance):
        return 0.0

    risk_amount = equity * (cfg.risk_pct / 100.0)
    qty = risk_amount / stop_distance

    max_notional = equity * cfg.max_leverage
    if qty * entry > max_notional:
        qty = max_notional / entry

    return qty if np.isfinite(qty) and qty > 0 else 0.0


# ══════════════════════════════════════════════════════════════════
#  백테스트 엔진
# ══════════════════════════════════════════════════════════════════

def run_backtest(ltf: pd.DataFrame, htf: pd.DataFrame, cfg) -> BacktestResult:
    """LTF/HTF 봉 데이터로 백테스트를 실행한다."""
    cfg.validate()

    n = len(ltf)
    open_ = ltf["open"].to_numpy(dtype=float)
    high = ltf["high"].to_numpy(dtype=float)
    low = ltf["low"].to_numpy(dtype=float)
    close = ltf["close"].to_numpy(dtype=float)
    index = ltf.index

    # ── 사전 계산 (전부 미래참조 없는 값들) ──
    atr = compute_atr(ltf, cfg.atr_period)
    struct = build_ltf_structure(ltf, cfg)
    ltf_swing_high, ltf_swing_low = last_confirmed_ltf_swings(ltf, cfg)
    ctx = align_htf_range(htf, index, cfg)
    all_fvgs = detect_fvgs(ltf)

    slip = cfg.slippage_bps / 10_000.0

    equity = cfg.initial_capital
    equity_curve = np.full(n, equity, dtype=float)
    trades = []

    active_fvgs = []      # 아직 진입 대기 중인 FVG들
    fvg_pointer = 0       # all_fvgs에서 어디까지 꺼냈는지
    position = None       # 열려 있는 포지션 (dict)

    # 진입이 왜 안 됐는지 집계 — 파라미터 튜닝할 때 병목을 찾는 용도
    rejections = {"구역_불일치": 0, "손익비_미달": 0, "목표_없음": 0,
                  "손절_역전": 0, "수량_0": 0, "갭_크기_미달": 0}

    for i in range(n):
        # ── 1) 직전 봉까지 확정된 FVG를 대기 목록에 추가 ──
        #    ⚠️ created_idx == i 인 FVG는 이번 봉이 마감돼야 알 수 있으므로
        #       아직 넣지 않는다. i-1 이하만 진입 후보가 된다.
        while fvg_pointer < len(all_fvgs) and all_fvgs[fvg_pointer].created_idx <= i - 1:
            candidate = all_fvgs[fvg_pointer]
            fvg_pointer += 1
            if passes_size_filter(candidate, atr[candidate.created_idx], cfg.min_fvg_atr):
                active_fvgs.append(candidate)
            else:
                rejections["갭_크기_미달"] += 1

        # ── 2) 열린 포지션의 손절/익절 확인 ──
        if position is not None:
            exit_info = _check_exit(position, high[i], low[i], slip, cfg)
            if exit_info is not None:
                equity = _close_position(position, exit_info, index[i], i, equity, trades, cfg)
                position = None

        # ── 3) 만료되거나 무효화된 FVG 정리 ──
        surviving = []
        for candidate in active_fvgs:
            if candidate.is_expired(i, cfg.fvg_max_age_bars):
                continue
            if candidate.is_invalidated(high[i], low[i], close[i], cfg.fvg_invalidate_on):
                continue
            surviving.append(candidate)
        active_fvgs = surviving

        # ── 4) 신규 진입 판단 ──
        if position is None and i >= cfg.warmup_bars:
            entry = _try_entry(
                i, open_, high, low, index, atr, struct, ctx,
                ltf_swing_high, ltf_swing_low, active_fvgs, equity, cfg, rejections,
            )
            if entry is not None:
                position, used_fvg = entry
                active_fvgs.remove(used_fvg)

                # 진입한 그 봉 안에서 이미 손절/익절에 닿았을 수 있다.
                # (특히 CE에 닿은 봉이 계속 밀려 손절까지 갔을 경우)
                exit_info = _check_exit(position, high[i], low[i], slip, cfg)
                if exit_info is not None:
                    equity = _close_position(position, exit_info, index[i], i, equity, trades, cfg)
                    position = None

        # ── 5) 평가 자산 기록 ──
        if position is not None:
            direction_sign = 1.0 if position["direction"] == BULLISH else -1.0
            unrealised = (close[i] - position["entry_price"]) * position["qty"] * direction_sign
            equity_curve[i] = equity + unrealised
        else:
            equity_curve[i] = equity

    # 마지막까지 안 닫힌 포지션은 마지막 종가로 정리한다.
    if position is not None:
        exit_info = {"price": close[n - 1], "reason": "종료시_청산", "fee_rate": cfg.taker_fee}
        equity = _close_position(position, exit_info, index[n - 1], n - 1, equity, trades, cfg)
        equity_curve[n - 1] = equity

    trades_df = _trades_to_frame(trades)
    curve = pd.Series(equity_curve, index=index, name="equity")
    metrics = compute_metrics(trades_df, curve, cfg)

    return BacktestResult(
        trades=trades_df,
        equity_curve=curve,
        metrics=metrics,
        config=cfg.to_dict(),
        rejections=rejections,
    )


def _try_entry(i, open_, high, low, index, atr, struct, ctx,
               ltf_swing_high, ltf_swing_low, active_fvgs, equity, cfg, rejections):
    """이번 봉에서 진입 조건을 만족하는 FVG가 있는지 찾는다.

    조건을 모두 통과해야 진입한다:
      ① 유동성 사냥 + 구조 전환(MSS)이 성립한 구간에서 만들어진 FVG인가
      ② 그 FVG가 디스카운트(롱) / 프리미엄(숏) 구역에 있는가
      ③ 가격이 CE에 닿았는가
      ④ 손절선이 진입가 반대편에 제대로 놓이는가
      ⑤ 목표까지의 손익비가 min_rr 이상인가
    """
    equilibrium = ctx["equilibrium"][i]
    bull_ok = cfg.allow_long and _setup_active(struct["bull_stage"][i], cfg)
    bear_ok = cfg.allow_short and _setup_active(struct["bear_stage"][i], cfg)

    if not bull_ok and not bear_ok:
        return None

    bull_since = struct["bull_since"][i]
    bear_since = struct["bear_since"][i]

    # CE에 가장 먼저 닿는(=가격에 가까운) FVG부터 검토한다.
    for candidate in sorted(active_fvgs, key=lambda f: -f.created_idx):
        if candidate.direction == BULLISH:
            if not bull_ok:
                continue
            # ① MSS 구간에서 생긴 갭인지 (require_sweep을 끄면 이 검사도 생략)
            if cfg.require_sweep and candidate.created_idx < bull_since - cfg.fvg_pre_mss_bars:
                continue
            # ② 디스카운트 구역인지
            if not in_discount_zone(candidate, equilibrium, cfg.zone_mode):
                rejections["구역_불일치"] += 1
                continue
        else:
            if not bear_ok:
                continue
            if cfg.require_sweep and candidate.created_idx < bear_since - cfg.fvg_pre_mss_bars:
                continue
            if not in_premium_zone(candidate, equilibrium, cfg.zone_mode):
                rejections["구역_불일치"] += 1
                continue

        # ③ CE 터치
        if not candidate.touched_ce(high[i], low[i]):
            continue

        entry_price = candidate.fill_price(open_[i])

        # ④ 손절선
        stop = _pick_stop(candidate.direction, candidate,
                          ltf_swing_high, ltf_swing_low, i, atr[i], cfg)
        if candidate.direction == BULLISH and not stop < entry_price:
            rejections["손절_역전"] += 1
            continue
        if candidate.direction == BEARISH and not stop > entry_price:
            rejections["손절_역전"] += 1
            continue

        # ⑤ 목표가와 손익비
        target = _pick_target(candidate.direction, entry_price, ctx, i, cfg)
        if not np.isfinite(target):
            rejections["목표_없음"] += 1
            continue

        reward = abs(target - entry_price)
        risk = abs(entry_price - stop)
        planned_rr = reward / risk if risk > 0 else 0.0
        if planned_rr < cfg.min_rr:
            rejections["손익비_미달"] += 1
            continue

        qty = _position_size(equity, entry_price, stop, cfg)
        if qty <= 0:
            rejections["수량_0"] += 1
            continue

        entry_fee = entry_price * qty * cfg.maker_fee
        position = {
            "direction": candidate.direction,
            "entry_time": index[i],
            "entry_idx": i,
            "entry_price": entry_price,
            "stop_price": stop,
            "target_price": target,
            "qty": qty,
            "entry_fee": entry_fee,
            "planned_rr": planned_rr,
            "fvg_top": candidate.top,
            "fvg_bottom": candidate.bottom,
            "fvg_ce": candidate.ce,
        }
        return position, candidate

    return None


def _check_exit(position, bar_high, bar_low, slip, cfg):
    """이번 봉에서 손절 또는 익절에 닿았는지 확인한다.

    ⚠️ 한 봉에서 둘 다 닿은 경우 '손절 먼저'로 처리한다.
       봉 안의 가격 순서를 알 수 없으므로, 불리한 쪽을 가정하는 게 안전하다.
    """
    if position["direction"] == BULLISH:
        if bar_low <= position["stop_price"]:
            price = position["stop_price"] * (1.0 - slip)  # 스탑은 밀려서 체결된다
            return {"price": price, "reason": "손절", "fee_rate": cfg.taker_fee}
        if bar_high >= position["target_price"]:
            return {"price": position["target_price"], "reason": "익절", "fee_rate": cfg.maker_fee}
        return None

    if bar_high >= position["stop_price"]:
        price = position["stop_price"] * (1.0 + slip)
        return {"price": price, "reason": "손절", "fee_rate": cfg.taker_fee}
    if bar_low <= position["target_price"]:
        return {"price": position["target_price"], "reason": "익절", "fee_rate": cfg.maker_fee}
    return None


def _close_position(position, exit_info, exit_time, exit_idx, equity, trades, cfg):
    """포지션을 닫고 손익을 확정해 자본에 반영한다."""
    exit_price = exit_info["price"]
    qty = position["qty"]
    sign = 1.0 if position["direction"] == BULLISH else -1.0

    gross = (exit_price - position["entry_price"]) * qty * sign
    exit_fee = exit_price * qty * exit_info["fee_rate"]
    fees = position["entry_fee"] + exit_fee
    net = gross - fees

    risk = abs(position["entry_price"] - position["stop_price"])
    r_multiple = net / (risk * qty) if risk > 0 and qty > 0 else 0.0

    equity += net

    trades.append(Trade(
        direction="LONG" if position["direction"] == BULLISH else "SHORT",
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
        r_multiple=r_multiple,
        planned_rr=position["planned_rr"],
        bars_held=exit_idx - position["entry_idx"],
        equity_after=equity,
        fvg_top=position["fvg_top"],
        fvg_bottom=position["fvg_bottom"],
        fvg_ce=position["fvg_ce"],
    ))
    return equity


def _trades_to_frame(trades) -> pd.DataFrame:
    columns = [
        "direction", "entry_time", "entry_price", "stop_price", "target_price", "qty",
        "exit_time", "exit_price", "exit_reason", "gross_pnl", "fees", "net_pnl",
        "r_multiple", "planned_rr", "bars_held", "equity_after",
        "fvg_top", "fvg_bottom", "fvg_ce",
    ]
    if not trades:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame([t.__dict__ for t in trades])[columns]


# ══════════════════════════════════════════════════════════════════
#  성과 지표
# ══════════════════════════════════════════════════════════════════

def compute_metrics(trades: pd.DataFrame, equity_curve: pd.Series, cfg) -> dict:
    """백테스트 결과를 숫자로 요약한다."""
    start_equity = cfg.initial_capital
    end_equity = float(equity_curve.iloc[-1]) if len(equity_curve) else start_equity

    # 최대 낙폭 — 자본 곡선의 고점 대비 최대 하락률
    running_peak = equity_curve.cummax()
    drawdown = equity_curve / running_peak - 1.0
    max_drawdown = float(drawdown.min()) if len(drawdown) else 0.0

    metrics = {
        "초기자본": start_equity,
        "최종자본": end_equity,
        "총수익률(%)": (end_equity / start_equity - 1.0) * 100.0,
        "최대낙폭(%)": max_drawdown * 100.0,
        "거래횟수": int(len(trades)),
    }

    if trades.empty:
        metrics.update({
            "승률(%)": 0.0, "손익비(PF)": 0.0, "기대값(R)": 0.0,
            "평균수익": 0.0, "평균손실": 0.0, "최대연속손실": 0,
            "롱거래": 0, "숏거래": 0, "평균보유봉수": 0.0,
            "샤프지수": 0.0, "CAGR(%)": 0.0,
        })
        return metrics

    wins = trades[trades["net_pnl"] > 0]
    losses = trades[trades["net_pnl"] <= 0]
    gross_profit = float(wins["net_pnl"].sum())
    gross_loss = float(-losses["net_pnl"].sum())

    # 연속 손실 최대 길이
    streak = worst_streak = 0
    for pnl in trades["net_pnl"]:
        if pnl <= 0:
            streak += 1
            worst_streak = max(worst_streak, streak)
        else:
            streak = 0

    metrics.update({
        "승률(%)": len(wins) / len(trades) * 100.0,
        "손익비(PF)": (gross_profit / gross_loss) if gross_loss > 0 else float("inf"),
        "기대값(R)": float(trades["r_multiple"].mean()),
        "평균수익": float(wins["net_pnl"].mean()) if len(wins) else 0.0,
        "평균손실": float(losses["net_pnl"].mean()) if len(losses) else 0.0,
        "최대연속손실": int(worst_streak),
        "롱거래": int((trades["direction"] == "LONG").sum()),
        "숏거래": int((trades["direction"] == "SHORT").sum()),
        "평균보유봉수": float(trades["bars_held"].mean()),
        "총수수료": float(trades["fees"].sum()),
        "익절횟수": int((trades["exit_reason"] == "익절").sum()),
        "손절횟수": int((trades["exit_reason"] == "손절").sum()),
    })

    # 샤프지수 / CAGR — 봉 단위 수익률을 연율화
    bars_per_year = (365.0 * 24.0 * 3600.0 * 1000.0) / timeframe_ms(cfg.ltf)
    returns = equity_curve.pct_change().dropna()
    if len(returns) > 1 and returns.std() > 0:
        metrics["샤프지수"] = float(returns.mean() / returns.std() * np.sqrt(bars_per_year))
    else:
        metrics["샤프지수"] = 0.0

    years = len(equity_curve) / bars_per_year
    if years > 0 and end_equity > 0:
        metrics["CAGR(%)"] = ((end_equity / start_equity) ** (1.0 / years) - 1.0) * 100.0
    else:
        metrics["CAGR(%)"] = 0.0

    return metrics


def format_report(result: BacktestResult) -> str:
    """콘솔에 출력할 성과 요약표를 만든다."""
    cfg = result.config
    lines = [
        "",
        "━" * 62,
        "  ICT High-Probability FVG 전략 — 백테스트 결과",
        "━" * 62,
        f"  종목        : {cfg['exchange']} {cfg['symbol']} ({cfg['market_type']})",
        f"  타임프레임  : HTF {cfg['htf']} / LTF {cfg['ltf']}",
        f"  필터        : 사냥={cfg['require_sweep']}  MSS={cfg['require_mss']}  "
        f"구역={cfg['zone_mode']}  최소RR={cfg['min_rr']}",
        f"  리스크      : 회당 {cfg['risk_pct']}%  최대레버리지 {cfg['max_leverage']}배",
    ]

    if len(result.equity_curve):
        lines.append(
            f"  기간        : {result.equity_curve.index[0]:%Y-%m-%d} ~ "
            f"{result.equity_curve.index[-1]:%Y-%m-%d}  ({len(result.equity_curve):,}봉)"
        )

    lines.append("─" * 62)

    for key, value in result.metrics.items():
        if isinstance(value, float):
            if value == float("inf"):
                shown = "∞"
            elif "%" in key or key in ("손익비(PF)", "기대값(R)", "샤프지수"):
                shown = f"{value:,.2f}"
            else:
                shown = f"{value:,.2f}"
        else:
            shown = f"{value:,}"
        lines.append(f"  {key:<14}: {shown:>18}")

    if result.rejections and any(result.rejections.values()):
        lines.append("─" * 62)
        lines.append("  진입 무산 사유 (파라미터 조정 참고용)")
        for key, value in result.rejections.items():
            if value:
                lines.append(f"    {key:<16}: {value:,}회")

    lines.append("━" * 62)
    lines.append("  ⚠️ 이 수치는 낙관적인 상한선입니다. 지정가 미체결, 자금조달 수수료,")
    lines.append("     봉 내부 가격 경로는 반영되지 않았습니다. 실전 투입 전 데모 검증 필수.")
    lines.append("━" * 62)
    return "\n".join(lines)
