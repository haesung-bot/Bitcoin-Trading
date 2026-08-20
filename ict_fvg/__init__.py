"""
ICT High-Probability FVG Strategy
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CCXT / Pandas 기반 ICT FVG 전략 및 백테스팅 패키지.

구성:
  config.py     설정값 (타임프레임, 필터, 손익비, 자금관리)
  data.py       CCXT OHLCV 수집 + CSV 캐싱
  structure.py  스윙, 유동성 사냥, MSS, 프리미엄/디스카운트
  fvg.py        FVG 탐지 및 생애주기
  backtest.py   백테스트 엔진 + 성과 지표
  strategy.py   실시간 신호 생성 (백테스트와 동일 로직)

빠른 시작:
    python run_ict_backtest.py --symbol BTC/USDT --start 2024-01-01
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from .config import StrategyConfig, timeframe_ms, TIMEFRAME_MS
from .data import fetch_ohlcv, load_csv, validate_ohlcv
from .fvg import FVG, BULLISH, BEARISH, detect_fvgs, to_dataframe as fvgs_to_dataframe
from .structure import (
    find_swings, swing_table, build_ltf_structure, align_htf_range, compute_atr,
)
from .backtest import run_backtest, BacktestResult, Trade, compute_metrics, format_report
from .strategy import scan_setups, describe_market_state, drop_unclosed_bar

__version__ = "1.0.0"

__all__ = [
    "StrategyConfig", "timeframe_ms", "TIMEFRAME_MS",
    "fetch_ohlcv", "load_csv", "validate_ohlcv",
    "FVG", "BULLISH", "BEARISH", "detect_fvgs", "fvgs_to_dataframe",
    "find_swings", "swing_table", "build_ltf_structure", "align_htf_range", "compute_atr",
    "run_backtest", "BacktestResult", "Trade", "compute_metrics", "format_report",
    "scan_setups", "describe_market_state", "drop_unclosed_bar",
]
