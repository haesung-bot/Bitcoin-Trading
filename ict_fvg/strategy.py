"""
ict_fvg/strategy.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
실시간 매매용 신호 생성 모듈.

백테스트(backtest.py)와 '똑같은 규칙'을 쓰되, 주문을 내는 대신
"지금 대기 중인 진입 자리"를 표로 돌려줍니다.
백테스트와 실전의 로직이 갈라지면 백테스트가 무의미해지므로,
필터 판정은 전부 fvg.py / structure.py의 같은 함수를 재사용합니다.

사용 예:
    from ict_fvg import StrategyConfig, fetch_ohlcv, scan_setups

    cfg = StrategyConfig(symbol="BTC/USDT", htf="1h", ltf="15m")
    ltf = fetch_ohlcv("binance", "BTC/USDT", "15m", start="2024-01-01")
    htf = fetch_ohlcv("binance", "BTC/USDT", "1h",  start="2024-01-01")

    setups = scan_setups(ltf, htf, cfg)
    print(setups)

⚠️ 반드시 '마감된 봉'만 넘기세요.
   진행 중인 봉을 포함하면 FVG와 스윙이 실시간으로 바뀌어 신호가 깜빡입니다.
   drop_unclosed_bar() 함수로 마지막 미완성 봉을 잘라낼 수 있습니다.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import numpy as np
import pandas as pd

from .config import timeframe_ms
from .backtest import _pick_stop, _pick_target, _position_size, _setup_active
from .fvg import (
    BULLISH, detect_fvgs, passes_size_filter,
    in_discount_zone, in_premium_zone,
)
from .structure import (
    build_ltf_structure, last_confirmed_ltf_swings, align_htf_range, compute_atr,
)


def drop_unclosed_bar(df: pd.DataFrame, timeframe: str,
                      now: pd.Timestamp = None) -> pd.DataFrame:
    """아직 마감되지 않은 마지막 봉을 잘라낸다.

    거래소 API는 진행 중인 봉도 같이 주기 때문에, 그대로 쓰면
    고가/저가가 계속 변해서 신호가 생겼다 사라졌다 합니다.
    """
    if df.empty:
        return df
    now = now or pd.Timestamp.utcnow().tz_localize("UTC")
    bar_delta = pd.Timedelta(milliseconds=timeframe_ms(timeframe))
    if df.index[-1] + bar_delta > now:
        return df.iloc[:-1]
    return df


def scan_setups(ltf: pd.DataFrame, htf: pd.DataFrame, cfg,
                equity: float = None) -> pd.DataFrame:
    """마지막 봉 기준으로 '살아 있는' 진입 후보를 전부 돌려준다.

    반환 컬럼:
      direction   LONG / SHORT
      entry       CE (지정가 주문을 걸 가격)
      stop        손절가
      target      익절가
      rr          손익비
      qty         equity를 넘겼다면 리스크 기준 권장 수량
      triggered   마지막 봉에서 이미 CE에 닿았는지 (True면 지금이 진입 시점)
      distance_%  현재가에서 CE까지 남은 거리
    """
    cfg.validate()

    if len(ltf) < max(cfg.warmup_bars, cfg.atr_period + 5):
        raise ValueError(
            f"LTF 봉이 너무 적습니다 ({len(ltf)}개). "
            f"최소 {max(cfg.warmup_bars, cfg.atr_period + 5)}개 이상 필요합니다."
        )

    equity = cfg.initial_capital if equity is None else equity

    high = ltf["high"].to_numpy(dtype=float)
    low = ltf["low"].to_numpy(dtype=float)
    close = ltf["close"].to_numpy(dtype=float)

    atr = compute_atr(ltf, cfg.atr_period)
    struct = build_ltf_structure(ltf, cfg)
    ltf_swing_high, ltf_swing_low = last_confirmed_ltf_swings(ltf, cfg)
    ctx = align_htf_range(htf, ltf.index, cfg)

    last = len(ltf) - 1
    active = _active_fvgs_at(ltf, atr, cfg, last)

    equilibrium = ctx["equilibrium"][last]
    current_price = close[last]

    bull_ok = cfg.allow_long and _setup_active(struct["bull_stage"][last], cfg)
    bear_ok = cfg.allow_short and _setup_active(struct["bear_stage"][last], cfg)
    bull_since = struct["bull_since"][last]
    bear_since = struct["bear_since"][last]

    rows = []
    for candidate in active:
        if candidate.direction == BULLISH:
            if not bull_ok:
                continue
            if cfg.require_sweep and candidate.created_idx < bull_since - cfg.fvg_pre_mss_bars:
                continue
            if not in_discount_zone(candidate, equilibrium, cfg.zone_mode):
                continue
        else:
            if not bear_ok:
                continue
            if cfg.require_sweep and candidate.created_idx < bear_since - cfg.fvg_pre_mss_bars:
                continue
            if not in_premium_zone(candidate, equilibrium, cfg.zone_mode):
                continue

        entry = candidate.ce
        stop = _pick_stop(candidate.direction, candidate,
                          ltf_swing_high, ltf_swing_low, last, atr[last], cfg)

        if candidate.direction == BULLISH and not stop < entry:
            continue
        if candidate.direction != BULLISH and not stop > entry:
            continue

        target = _pick_target(candidate.direction, entry, ctx, last, cfg)
        if not np.isfinite(target):
            continue

        risk = abs(entry - stop)
        rr = abs(target - entry) / risk if risk > 0 else 0.0
        if rr < cfg.min_rr:
            continue

        rows.append({
            "direction": "LONG" if candidate.direction == BULLISH else "SHORT",
            "fvg_time": candidate.created_time,
            "fvg_top": candidate.top,
            "fvg_bottom": candidate.bottom,
            "entry": entry,
            "stop": stop,
            "target": target,
            "rr": rr,
            "qty": _position_size(equity, entry, stop, cfg),
            "risk_amount": equity * (cfg.risk_pct / 100.0),
            "triggered": bool(candidate.touched_ce(high[last], low[last])),
            "distance_%": (entry - current_price) / current_price * 100.0,
            "age_bars": last - candidate.created_idx,
        })

    columns = ["direction", "fvg_time", "fvg_top", "fvg_bottom", "entry", "stop",
               "target", "rr", "qty", "risk_amount", "triggered", "distance_%", "age_bars"]
    if not rows:
        return pd.DataFrame(columns=columns)

    result = pd.DataFrame(rows)[columns]
    # 지금 당장 체결 가능한 것 → 가격에 가까운 것 순으로 정렬
    return result.sort_values(
        ["triggered", "distance_%"],
        ascending=[False, True],
        key=lambda s: s.abs() if s.name == "distance_%" else s,
    ).reset_index(drop=True)


def _active_fvgs_at(ltf: pd.DataFrame, atr, cfg, at_idx: int) -> list:
    """at_idx 시점에 아직 살아 있는(만료·무효화되지 않은) FVG 목록을 만든다.

    백테스트 엔진의 생애주기 관리와 동일한 규칙을 적용한다.
    """
    high = ltf["high"].to_numpy(dtype=float)
    low = ltf["low"].to_numpy(dtype=float)
    close = ltf["close"].to_numpy(dtype=float)

    all_fvgs = detect_fvgs(ltf)
    active = []
    pointer = 0

    for i in range(at_idx + 1):
        while pointer < len(all_fvgs) and all_fvgs[pointer].created_idx <= i - 1:
            candidate = all_fvgs[pointer]
            pointer += 1
            if passes_size_filter(candidate, atr[candidate.created_idx], cfg.min_fvg_atr):
                active.append(candidate)

        active = [
            f for f in active
            if not f.is_expired(i, cfg.fvg_max_age_bars)
            and not f.is_invalidated(high[i], low[i], close[i], cfg.fvg_invalidate_on)
        ]

    return active


def describe_market_state(ltf: pd.DataFrame, htf: pd.DataFrame, cfg) -> dict:
    """지금 시장이 어떤 상태인지 사람이 읽을 수 있게 요약한다.
    신호가 왜 안 나오는지 확인할 때 유용하다."""
    cfg.validate()

    struct = build_ltf_structure(ltf, cfg)
    ctx = align_htf_range(htf, ltf.index, cfg)
    last = len(ltf) - 1
    price = float(ltf["close"].iloc[-1])
    equilibrium = ctx["equilibrium"][last]

    stage_label = {0: "없음", 1: "유동성 사냥 완료 (MSS 대기)", 2: "MSS 확인 (진입 대기)"}

    if not np.isfinite(equilibrium):
        zone = "판단 불가 (HTF 레인지 미확정)"
    elif price > equilibrium:
        zone = "프리미엄 (숏 우호)"
    else:
        zone = "디스카운트 (롱 우호)"

    return {
        "시각": ltf.index[-1],
        "현재가": price,
        "HTF_레인지_상단": ctx["range_high"][last],
        "HTF_레인지_하단": ctx["range_low"][last],
        "HTF_균형점": equilibrium,
        "현재_구역": zone,
        "상승_시나리오": stage_label[int(struct["bull_stage"][last])],
        "하락_시나리오": stage_label[int(struct["bear_stage"][last])],
        "직전_LTF_스윙고점": struct["swing_high"][last],
        "직전_LTF_스윙저점": struct["swing_low"][last],
    }
