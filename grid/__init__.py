"""
그리드 트레이딩 백테스트
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  backtest.py  격자 슬롯 모델, 체결 경로 시뮬레이션, 손익 분해

⚠️ 두 가지를 항상 함께 보십시오:
   1) 체결 가정 세 가지의 결과 폭 — 폭이 크면 그 백테스트는 못 믿습니다
   2) 실현손익과 평가손익의 분리 — 그리드 수익만 보면 반드시 속습니다
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from .backtest import (
    run_rolling_grid,
    GridConfig, GridResult, FILL_ASSUMPTIONS,
    build_levels, run_grid_backtest, compare_fill_assumptions, format_report,
)

__all__ = [
    "GridConfig", "GridResult", "FILL_ASSUMPTIONS",
    "build_levels", "run_grid_backtest", "run_rolling_grid", "compare_fill_assumptions", "format_report",
]
