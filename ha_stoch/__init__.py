"""
하이킨 애쉬 + EMA200 + 스토캐스틱 RSI 전략
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  indicators.py  하이킨 애쉬, EMA, RSI, 스토캐스틱 RSI, ATR
  backtest.py    신호 생성 및 백테스트 (ICT 엔진과 동일한 회계 규칙)

⚠️ 하이킨 애쉬 값은 신호 판정에만 씁니다. 체결가는 항상 원본 가격입니다.
   HA 종가는 평균값이라 시장에 존재한 적이 없기 때문입니다.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from .indicators import heikin_ashi, ema, rsi, stoch_rsi, atr
from .backtest import HAStochConfig, build_signals, run_backtest

__all__ = [
    "heikin_ashi", "ema", "rsi", "stoch_rsi", "atr",
    "HAStochConfig", "build_signals", "run_backtest",
]
