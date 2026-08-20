"""
ict_fvg/fvg.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FVG(Fair Value Gap, 공정가치 갭) 탐지 및 생애주기 관리.

FVG는 3개의 연속된 봉으로 정의됩니다. 가운데 봉이 크게 움직여서
첫 봉과 셋째 봉의 가격대가 서로 겹치지 않는 구간이 생기면 그게 갭입니다.

  상승 FVG (Bullish):  Candle[i-2].high < Candle[i].low
      상단  = Candle[i].low
      하단  = Candle[i-2].high
      CE    = (상단 + 하단) / 2      ← 실제 진입 가격

  하락 FVG (Bearish):  Candle[i-2].low > Candle[i].high
      상단  = Candle[i-2].low
      하단  = Candle[i].high
      CE    = (상단 + 하단) / 2

⚠️ FVG는 세 번째 봉(i)이 마감돼야 확정된다. 따라서 봉 i에서 발견한 FVG로
   진입할 수 있는 가장 빠른 시점은 봉 i+1이다. 백테스트 엔진이 이 규칙을 지킨다.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


BULLISH = 1
BEARISH = -1


@dataclass
class FVG:
    """탐지된 FVG 하나. 백테스트 중에는 이 객체가 '대기 중인 주문'처럼 취급된다."""
    direction: int          # BULLISH(1) 또는 BEARISH(-1)
    created_idx: int        # 세 번째 봉의 위치 (= 확정 시점)
    top: float
    bottom: float
    ce: float               # 0.5 레벨 — 진입 가격
    size: float             # 상단 - 하단
    created_time: pd.Timestamp = field(default=None)

    def is_expired(self, bar_idx: int, max_age: int) -> bool:
        return bar_idx - self.created_idx > max_age

    def is_invalidated(self, bar_high: float, bar_low: float,
                       bar_close: float, mode: str) -> bool:
        """FVG가 무효화됐는지 판정한다.

        상승 FVG는 가격이 갭 하단 아래로 내려가면 '지지 실패'로 보고 폐기한다.
        mode='close'면 종가 기준(꼬리로 잠깐 뚫는 건 허용), 'wick'이면 꼬리도 불허.
        """
        if self.direction == BULLISH:
            probe = bar_close if mode == "close" else bar_low
            return probe < self.bottom
        probe = bar_close if mode == "close" else bar_high
        return probe > self.top

    def touched_ce(self, bar_high: float, bar_low: float) -> bool:
        """이번 봉에서 가격이 CE에 닿았는지."""
        if self.direction == BULLISH:
            return bar_low <= self.ce
        return bar_high >= self.ce

    def fill_price(self, bar_open: float) -> float:
        """CE에 걸어둔 지정가 주문의 실제 체결가.

        보통은 CE에 체결되지만, 시가가 이미 CE를 넘어서 열렸다면(갭)
        그보다 유리한 시가에 체결된다.
        """
        if self.direction == BULLISH:
            return min(self.ce, bar_open)
        return max(self.ce, bar_open)


def detect_fvgs(df: pd.DataFrame) -> list:
    """전체 구간에서 FVG를 한 번에 찾아 리스트로 돌려준다.

    반환 리스트는 created_idx 오름차순으로 정렬돼 있어서,
    백테스트 엔진이 포인터 하나로 순차 소비할 수 있다.
    """
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    index = df.index
    n = len(df)

    if n < 3:
        return []

    # shift(2)에 해당하는 배열 — i 위치에서 i-2 봉의 값
    high_2 = np.full(n, np.nan)
    low_2 = np.full(n, np.nan)
    high_2[2:] = high[:-2]
    low_2[2:] = low[:-2]

    with np.errstate(invalid="ignore"):
        bull_mask = high_2 < low     # Candle[i-2].high < Candle[i].low
        bear_mask = low_2 > high     # Candle[i-2].low  > Candle[i].high

    results = []

    for i in np.flatnonzero(bull_mask):
        top, bottom = float(low[i]), float(high_2[i])
        results.append(FVG(
            direction=BULLISH,
            created_idx=int(i),
            top=top,
            bottom=bottom,
            ce=(top + bottom) / 2.0,
            size=top - bottom,
            created_time=index[i],
        ))

    for i in np.flatnonzero(bear_mask):
        top, bottom = float(low_2[i]), float(high[i])
        results.append(FVG(
            direction=BEARISH,
            created_idx=int(i),
            top=top,
            bottom=bottom,
            ce=(top + bottom) / 2.0,
            size=top - bottom,
            created_time=index[i],
        ))

    results.sort(key=lambda f: (f.created_idx, -f.direction))
    return results


def passes_size_filter(fvg: FVG, atr_value: float, min_fvg_atr: float) -> bool:
    """ATR 대비 너무 얇은 갭은 노이즈로 보고 버린다.
    ATR이 아직 계산되지 않은 워밍업 구간(NaN)은 통과시키지 않는다."""
    if min_fvg_atr <= 0:
        return True
    if not np.isfinite(atr_value) or atr_value <= 0:
        return False
    return fvg.size >= min_fvg_atr * atr_value


def in_discount_zone(fvg: FVG, equilibrium: float, mode: str) -> bool:
    """상승 FVG가 디스카운트 구역(레인지 하단 50%)에 있는지.

    mode='ce'   → CE만 균형점 아래면 통과
    mode='full' → 갭 전체(상단까지)가 균형점 아래여야 통과
    """
    if not np.isfinite(equilibrium):
        return False
    return (fvg.top if mode == "full" else fvg.ce) <= equilibrium


def in_premium_zone(fvg: FVG, equilibrium: float, mode: str) -> bool:
    """하락 FVG가 프리미엄 구역(레인지 상단 50%)에 있는지."""
    if not np.isfinite(equilibrium):
        return False
    return (fvg.bottom if mode == "full" else fvg.ce) >= equilibrium


def to_dataframe(fvgs: list) -> pd.DataFrame:
    """탐지 결과를 표로. 디버깅이나 시각화용."""
    if not fvgs:
        return pd.DataFrame(
            columns=["created_time", "created_idx", "direction", "top", "bottom", "ce", "size"]
        )
    return pd.DataFrame([{
        "created_time": f.created_time,
        "created_idx": f.created_idx,
        "direction": "bullish" if f.direction == BULLISH else "bearish",
        "top": f.top,
        "bottom": f.bottom,
        "ce": f.ce,
        "size": f.size,
    } for f in fvgs])
