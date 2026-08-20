"""
ha_stoch/indicators.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
하이킨 애쉬 / EMA / 스토캐스틱 RSI 계산.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ 하이킨 애쉬 백테스트의 가장 흔한 함정
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HA 종가는 (시가+고가+저가+종가)/4 의 '평균값'입니다.
이 가격은 시장에 실제로 존재한 적이 없습니다.

그런데 많은 백테스트가 HA 캔들로 신호를 뽑은 뒤 그대로 HA 가격에
체결시킵니다. 그러면 실제로는 낼 수 없는 주문이 체결된 것으로 계산되어
수익률이 크게 부풀려집니다. HA는 평활화된 지표라 이 오차가 항상
유리한 방향으로 쏠립니다.

그래서 이 모듈은 HA 값을 'ha_' 접두사가 붙은 별도 컬럼으로만 돌려줍니다.
체결가는 반드시 원본 open/high/low/close를 쓰십시오.
(backtest.py가 이 규칙을 지키고, 테스트가 이를 검증합니다)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import numpy as np
import pandas as pd


def heikin_ashi(df: pd.DataFrame) -> pd.DataFrame:
    """하이킨 애쉬 캔들을 계산한다.

        HA종가 = (시가 + 고가 + 저가 + 종가) / 4
        HA시가 = (직전 HA시가 + 직전 HA종가) / 2
        HA고가 = max(고가, HA시가, HA종가)
        HA저가 = min(저가, HA시가, HA종가)

    반환: ha_open / ha_high / ha_low / ha_close 컬럼을 가진 DataFrame
          (인덱스는 원본과 동일)
    """
    open_ = df["open"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    n = len(df)

    ha_close = (open_ + high + low + close) / 4.0
    ha_open = np.empty(n, dtype=float)

    # 첫 봉은 직전 HA 값이 없으므로 원본 시가/종가의 평균으로 시작한다
    ha_open[0] = (open_[0] + close[0]) / 2.0
    # HA시가는 직전 값에 의존하므로 순차 계산이 불가피하다
    for i in range(1, n):
        ha_open[i] = (ha_open[i - 1] + ha_close[i - 1]) / 2.0

    ha_high = np.maximum.reduce([high, ha_open, ha_close])
    ha_low = np.minimum.reduce([low, ha_open, ha_close])

    return pd.DataFrame(
        {"ha_open": ha_open, "ha_high": ha_high, "ha_low": ha_low, "ha_close": ha_close},
        index=df.index,
    )


def ema(series: pd.Series, period: int) -> np.ndarray:
    """지수이동평균. period개가 모이기 전 구간은 NaN으로 둔다
    (덜 익은 값으로 추세를 판단하면 초기 구간 성과가 왜곡된다)."""
    return series.ewm(span=period, adjust=False, min_periods=period).mean().to_numpy(dtype=float)


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """와일더 방식 RSI."""
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)

    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()

    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    result = 100.0 - (100.0 / (1.0 + rs))
    # 손실이 0인 구간(계속 상승)은 RSI 100으로 본다
    result[(avg_loss == 0) & (avg_gain > 0)] = 100.0
    result[(avg_loss == 0) & (avg_gain == 0)] = 50.0
    return result


def stoch_rsi(series: pd.Series, rsi_period: int = 14, stoch_period: int = 14,
              k_smooth: int = 3, d_smooth: int = 3):
    """스토캐스틱 RSI.

    RSI를 다시 스토캐스틱으로 정규화한 지표라, 일반 RSI보다 훨씬 자주
    0/100에 붙습니다. 그래서 과매도 기준을 20으로 잡아도 신호가 많습니다.

        StochRSI = (RSI - RSI최저) / (RSI최고 - RSI최저)
        K = SMA(StochRSI, k_smooth) × 100
        D = SMA(K, d_smooth)

    반환: (K, D) — 둘 다 0~100 범위의 numpy 배열
    """
    rsi_values = rsi(series, rsi_period)
    lowest = rsi_values.rolling(stoch_period).min()
    highest = rsi_values.rolling(stoch_period).max()

    span = (highest - lowest).replace(0.0, np.nan)
    raw = (rsi_values - lowest) / span
    # RSI가 구간 내내 평평하면(고가=저가) 방향을 알 수 없으므로 중립 0.5로 둔다
    raw = raw.fillna(0.5)

    k = (raw.rolling(k_smooth).mean() * 100.0)
    d = k.rolling(d_smooth).mean()

    # 워밍업이 끝나지 않은 구간은 NaN을 유지해야 신호가 잘못 나오지 않는다
    warmup = rsi_period + stoch_period + k_smooth + d_smooth
    k = k.copy()
    d = d.copy()
    k.iloc[:warmup] = np.nan
    d.iloc[:warmup] = np.nan

    return k.to_numpy(dtype=float), d.to_numpy(dtype=float)


def atr(df: pd.DataFrame, period: int = 14) -> np.ndarray:
    """ATR. 손절폭 산정에 쓴다."""
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)

    true_range = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    return true_range.ewm(alpha=1.0 / period, adjust=False,
                          min_periods=period).mean().to_numpy(dtype=float)
