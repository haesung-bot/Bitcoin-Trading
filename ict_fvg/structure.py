"""
ict_fvg/structure.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
시장 구조(Market Structure) 판단 모듈.

이 파일이 담당하는 것:
  1. 스윙 고점/저점 (프랙탈)
  2. 유동성 사냥(Liquidity Sweep)과 종가 이탈(Break)의 구분
  3. 구조 전환 (MSS, Market Structure Shift)
  4. 딜링 레인지와 프리미엄/디스카운트 구역

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ 미래참조(lookahead) 주의사항 — 백테스트 신뢰도의 핵심
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
스윙 고점은 "오른쪽 N개 봉이 마감돼야" 비로소 확정됩니다.
차트를 눈으로 볼 때는 이미 다 지나간 뒤라 당연해 보이지만,
실시간으로는 N봉 뒤에야 알 수 있습니다.

이 파일의 모든 함수는 스윙이 발생한 시점(i)이 아니라
확정된 시점(i + right)부터만 그 값을 쓰도록 되어 있습니다.
이 규칙을 어기면 백테스트 수익률이 실전과 완전히 달라집니다.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import numpy as np
import pandas as pd


def find_swings(df: pd.DataFrame, left: int, right: int):
    """프랙탈 방식으로 스윙 고점/저점을 찾는다.

    봉 i가 스윙 고점 = high[i]가 [i-left, i+right] 구간에서 최댓값.

    반환값: (is_swing_high, is_swing_low) — 둘 다 길이 n의 bool 배열.
    ⚠️ 이 배열의 True는 "그 봉이 스윙이다"라는 뜻일 뿐,
       "그 시점에 알 수 있다"는 뜻이 아니다. 확정 시점은 i + right 이다.
    """
    high = df["high"]
    low = df["low"]
    window = left + right + 1

    # rolling(window)의 결과는 구간의 오른쪽 끝(i+right)에 놓이므로,
    # shift(-right)로 중앙(i)으로 끌어온다.
    roll_max = high.rolling(window).max().shift(-right)
    roll_min = low.rolling(window).min().shift(-right)

    is_high = high.eq(roll_max).to_numpy(copy=True)
    is_low = low.eq(roll_min).to_numpy(copy=True)

    # 윈도가 다 채워지지 않는 양 끝은 스윙으로 인정하지 않는다.
    is_high[:left] = False
    is_low[:left] = False
    if right > 0:
        is_high[len(is_high) - right:] = False
        is_low[len(is_low) - right:] = False

    return is_high, is_low


def swing_table(df: pd.DataFrame, left: int, right: int, tf_ms: int) -> pd.DataFrame:
    """스윙을 표로 만든다. HTF 스윙을 LTF 타임라인에 붙일 때 사용한다.

    컬럼:
      kind         'high' / 'low'
      bar_idx      스윙이 발생한 봉의 위치
      level        스윙 가격
      available_at 이 스윙을 "실시간으로 알 수 있게 되는" 시각.
                   = 확정 봉(bar_idx + right)의 종가 시각
                   = index[bar_idx + right] + 타임프레임

    ⚠️ available_at을 스윙 발생 시각으로 잡으면 미래참조가 된다.
       확정 지연(right봉)과 봉 마감 지연(tf_ms)을 둘 다 더해야 한다.
    """
    is_high, is_low = find_swings(df, left, right)
    index = df.index
    tf_delta = pd.Timedelta(milliseconds=tf_ms)
    n = len(df)

    rows = []
    for kind, mask, series in (("high", is_high, df["high"]), ("low", is_low, df["low"])):
        for i in np.flatnonzero(mask):
            confirm_bar = i + right
            if confirm_bar >= n:
                continue  # 아직 확정되지 않은 스윙 — 사용하지 않는다
            rows.append({
                "kind": kind,
                "bar_idx": int(i),
                "level": float(series.iat[i]),
                "swing_time": index[i],
                "available_at": index[confirm_bar] + tf_delta,
            })

    if not rows:
        return pd.DataFrame(
            columns=["kind", "bar_idx", "level", "swing_time", "available_at"]
        )

    table = pd.DataFrame(rows).sort_values("available_at", kind="mergesort")
    return table.reset_index(drop=True)


def build_ltf_structure(df: pd.DataFrame, cfg) -> dict:
    """LTF 봉을 앞에서부터 한 번 훑으면서 시장 구조 상태를 만든다.

    ━━━ 사냥(Sweep)과 이탈(Break)의 구분 ━━━
    ICT에서 "외부 유동성을 건드렸다"와 "구조가 진짜 깨졌다"는 전혀 다른 사건이다.
    이 둘을 구분하지 않으면 규칙이 서로 모순된다. 여기서는 종가로 판단한다:

      스윙 저점 아래로 내려갔을 때
        · 종가는 저점 위로 회복  → 매도측 유동성 '사냥'  → 상승 시나리오 시작
        · 종가도 저점 아래       → 진짜 하락 '이탈'      → 상승 시나리오 폐기

      스윙 고점 위로 올라갔을 때
        · 종가는 고점 아래로 회복 → 매수측 유동성 '사냥' → 하락 시나리오 시작
        · 종가도 고점 위          → 진짜 상승 '이탈'     → 상승 MSS 성립

    ━━━ 셋업 단계 ━━━
      0단계: 없음
      1단계: 유동성 사냥 발생 → 구조 전환(MSS) 대기
      2단계: MSS 확인 → 이 구간에서 생긴 FVG만 진입 후보로 인정

    반환: 길이 n의 numpy 배열들을 담은 dict
      swing_high / swing_low : 그 시점까지 확정된 직전 스윙 레벨 (없으면 NaN)
      bull_stage / bear_stage: 위의 단계 (0/1/2)
      bull_since / bear_since: 현재 단계가 시작된 봉 위치 (없으면 -1)
    """
    n = len(df)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)

    left, right = cfg.ltf_swing_left, cfg.ltf_swing_right
    is_high, is_low = find_swings(df, left, right)

    out_swing_high = np.full(n, np.nan)
    out_swing_low = np.full(n, np.nan)
    out_bull_stage = np.zeros(n, dtype=np.int8)
    out_bear_stage = np.zeros(n, dtype=np.int8)
    out_bull_since = np.full(n, -1, dtype=np.int64)
    out_bear_since = np.full(n, -1, dtype=np.int64)

    cur_sh = np.nan   # 지금까지 확정됐고 아직 건드려지지 않은 스윙 고점
    cur_sl = np.nan   # 지금까지 확정됐고 아직 건드려지지 않은 스윙 저점
    bull_stage = bear_stage = 0
    bull_since = bear_since = -1

    for i in range(n):
        # ── 1) right봉이 지났으므로 i-right 봉의 스윙이 지금 확정된다 ──
        confirmed = i - right
        if confirmed >= 0:
            if is_high[confirmed]:
                cur_sh = high[confirmed]
            if is_low[confirmed]:
                cur_sl = low[confirmed]

        # ── 2) 셋업 유효기간 만료 처리 ──
        if bull_stage and i - bull_since > cfg.setup_valid_bars:
            bull_stage, bull_since = 0, -1
        if bear_stage and i - bear_since > cfg.setup_valid_bars:
            bear_stage, bear_since = 0, -1

        # ── 3) 스윙 저점 하향 이탈: 사냥인가 진짜 이탈인가 ──
        if not np.isnan(cur_sl) and low[i] < cur_sl:
            if close[i] > cur_sl:
                # 꼬리로만 찍고 회복 → 매도측 유동성 사냥 → 상승 시나리오 1단계
                bull_stage, bull_since = 1, i
            else:
                # 종가로 뚫림 → 진짜 하락 이탈. 상승 시나리오는 폐기.
                bull_stage, bull_since = 0, -1
                # 매수측 사냥을 기다리고 있었다면 이것이 하락 MSS다.
                if bear_stage == 1:
                    bear_stage, bear_since = 2, i
            cur_sl = np.nan  # 어느 쪽이든 이 유동성은 소진됐다

        # ── 4) 스윙 고점 상향 이탈: 사냥인가 진짜 이탈인가 ──
        if not np.isnan(cur_sh) and high[i] > cur_sh:
            if close[i] < cur_sh:
                # 꼬리로만 찍고 회복 → 매수측 유동성 사냥 → 하락 시나리오 1단계
                bear_stage, bear_since = 1, i
            else:
                # 종가로 뚫림 → 진짜 상승 이탈. 하락 시나리오는 폐기.
                bear_stage, bear_since = 0, -1
                # 매도측 사냥을 기다리고 있었다면 이것이 상승 MSS다.
                if bull_stage == 1:
                    bull_stage, bull_since = 2, i
            cur_sh = np.nan

        out_swing_high[i] = cur_sh
        out_swing_low[i] = cur_sl
        out_bull_stage[i] = bull_stage
        out_bear_stage[i] = bear_stage
        out_bull_since[i] = bull_since
        out_bear_since[i] = bear_since

    return {
        "swing_high": out_swing_high,
        "swing_low": out_swing_low,
        "bull_stage": out_bull_stage,
        "bear_stage": out_bear_stage,
        "bull_since": out_bull_since,
        "bear_since": out_bear_since,
    }


def last_confirmed_ltf_swings(df: pd.DataFrame, cfg) -> tuple:
    """손절선 계산용. 각 봉 시점에서 '확정된 직전 스윙 저점/고점'을 돌려준다.

    build_ltf_structure의 swing_high/swing_low는 사냥이 일어나면 NaN으로 소진되지만,
    손절선은 사냥 여부와 무관하게 직전 스윙 자리에 둬야 하므로 별도로 계산한다.
    """
    n = len(df)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    left, right = cfg.ltf_swing_left, cfg.ltf_swing_right
    is_high, is_low = find_swings(df, left, right)

    sh = np.full(n, np.nan)
    sl = np.full(n, np.nan)
    cur_h = cur_l = np.nan

    for i in range(n):
        confirmed = i - right
        if confirmed >= 0:
            if is_high[confirmed]:
                cur_h = high[confirmed]
            if is_low[confirmed]:
                cur_l = low[confirmed]
        sh[i] = cur_h
        sl[i] = cur_l

    return sh, sl


def _epoch_ns(values) -> np.ndarray:
    """시각 배열을 정수 나노초로 바꾼다.

    pandas 버전에 따라 내부 시각 해상도가 ns/us로 달라지고, tz-aware 값은
    numpy 변환 시 object 배열이 되기도 한다. 그 상태로 비교하면 버전에 따라
    조용히 결과가 달라지므로, 비교 전에 항상 ns 정수로 통일한다.
    """
    index = pd.DatetimeIndex(values)
    if index.tz is not None:
        index = index.tz_convert("UTC").tz_localize(None)
    return index.to_numpy(dtype="datetime64[ns]").astype("int64")


def _htf_dealing_range(htf: pd.DataFrame, cfg):
    """HTF 봉마다 딜링 레인지(스윙 파동 구간)를 계산한다.

    ━━━ 왜 스윙 값을 그대로 쓰지 않는가 ━━━
    "가장 최근 확정 스윙 고점"과 "가장 최근 확정 스윙 저점"을 따로 가져오면,
    두 스윙이 시간상 한참 떨어져 있을 때 현재가가 레인지 밖으로 나가버린다.
    (예: 레인지 27,720~27,890 인데 현재가는 25,850)
    이 상태의 0.5 레벨은 프리미엄/디스카운트 판정에 아무 의미가 없다.

    그래서 스윙이 확정된 뒤 가격이 실제로 그 밖으로 이탈했다면, 이탈한 만큼
    레인지를 넓혀준다. 이미 마감된 봉의 고가/저가만 쓰므로 미래참조가 아니다.
    새 스윙이 확정되면 그 방향의 경계는 새 스윙 값으로 다시 잡힌다.

    반환: (range_high, range_low, atr) — 모두 HTF 길이의 배열
    """
    n = len(htf)
    high = htf["high"].to_numpy(dtype=float)
    low = htf["low"].to_numpy(dtype=float)
    right = cfg.htf_swing_right
    is_high, is_low = find_swings(htf, cfg.htf_swing_left, right)

    range_high = np.full(n, np.nan)
    range_low = np.full(n, np.nan)
    cur_high = cur_low = np.nan

    for i in range(n):
        # right봉이 지났으므로 i-right 봉의 스윙이 지금 확정된다.
        confirmed = i - right
        if confirmed >= 0:
            if is_high[confirmed]:
                cur_high = high[confirmed]   # 새 스윙 고점 → 상단 재설정
            if is_low[confirmed]:
                cur_low = low[confirmed]

        # 확정 이후의 실제 이탈분을 반영해 레인지를 넓힌다.
        if np.isfinite(cur_high):
            cur_high = max(cur_high, high[i])
        if np.isfinite(cur_low):
            cur_low = min(cur_low, low[i])

        range_high[i] = cur_high
        range_low[i] = cur_low

    return range_high, range_low, compute_atr(htf, cfg.atr_period)


def align_htf_range(htf: pd.DataFrame, ltf_index: pd.DatetimeIndex, cfg) -> dict:
    """HTF 딜링 레인지를 LTF 타임라인에 붙인다.

    ⚠️ 여기가 미래참조가 가장 잘 생기는 자리다.
       HTF 봉은 '마감된 뒤'에야 알 수 있고, 스윙은 거기서 right봉을 더 기다려야 확정된다.
       그래서 스윙 발생 시각이 아니라 swing_table이 계산해 둔 available_at
       (확정 봉의 종가 시각)을 기준으로 붙인다.

    반환: LTF 길이의 배열들
      range_high / range_low : 그 시점의 딜링 레인지 상단/하단
      equilibrium            : 레인지의 0.5 레벨 (프리미엄/디스카운트 경계).
                               레인지가 너무 좁거나 깨졌으면 NaN
      sh_levels              : 확정된 HTF 스윙 고점 전체 (익절 목표 탐색용)
      sh_count               : 각 LTF 봉 시점까지 사용 가능한 스윙 고점 개수
      sl_levels / sl_count   : 스윙 저점 쪽 동일 자료
    """
    from .config import timeframe_ms

    table = swing_table(htf, cfg.htf_swing_left, cfg.htf_swing_right, timeframe_ms(cfg.htf))
    n = len(ltf_index)

    range_high = np.full(n, np.nan)
    range_low = np.full(n, np.nan)

    highs = table[table["kind"] == "high"].reset_index(drop=True)
    lows = table[table["kind"] == "low"].reset_index(drop=True)

    sh_levels = highs["level"].to_numpy(dtype=float) if len(highs) else np.empty(0)
    sl_levels = lows["level"].to_numpy(dtype=float) if len(lows) else np.empty(0)

    # 각 LTF 봉 시점까지 "이미 확정된" 스윙이 몇 개인지 세어 둔다.
    # side='right'이므로 available_at == 봉 시각인 경우도 사용 가능으로 본다.
    ltf_ns = _epoch_ns(ltf_index)
    if len(highs):
        sh_count = np.searchsorted(_epoch_ns(highs["available_at"]), ltf_ns, side="right")
    else:
        sh_count = np.zeros(n, dtype=np.int64)
    if len(lows):
        sl_count = np.searchsorted(_epoch_ns(lows["available_at"]), ltf_ns, side="right")
    else:
        sl_count = np.zeros(n, dtype=np.int64)

    # 딜링 레인지는 HTF 봉 단위로 먼저 계산한 뒤 LTF에 붙인다.
    htf_range_high, htf_range_low, htf_atr = _htf_dealing_range(htf, cfg)

    # HTF 봉은 '마감된 뒤'에야 알 수 있으므로 종가 시각 기준으로 붙인다.
    htf_close_ns = _epoch_ns(htf.index) + timeframe_ms(cfg.htf) * 1_000_000
    slot = np.searchsorted(htf_close_ns, ltf_ns, side="right") - 1
    usable = slot >= 0

    range_high[usable] = htf_range_high[slot[usable]]
    range_low[usable] = htf_range_low[slot[usable]]
    atr_aligned = np.full(n, np.nan)
    atr_aligned[usable] = htf_atr[slot[usable]]

    with np.errstate(invalid="ignore"):
        equilibrium = (range_high + range_low) / 2.0
        width = range_high - range_low
        # 레인지가 뒤집혔거나(고점 <= 저점), HTF 변동성 대비 너무 좁으면
        # 균형점이 의미가 없으므로 '판단 불가'로 둔다.
        broken = ~(width > 0)
        too_narrow = width < cfg.min_range_atr * atr_aligned
        equilibrium[broken | too_narrow] = np.nan

    return {
        "range_high": range_high,
        "range_low": range_low,
        "equilibrium": equilibrium,
        "sh_levels": sh_levels,
        "sh_count": sh_count,
        "sl_levels": sl_levels,
        "sl_count": sl_count,
    }


def compute_atr(df: pd.DataFrame, period: int) -> np.ndarray:
    """ATR(평균 실체 변동폭). FVG 최소 크기 필터와 손절 여유폭에 쓴다.
    와일더 방식(EMA alpha=1/period)을 사용한다."""
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)

    true_range = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr = true_range.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    return atr.to_numpy(dtype=float)
