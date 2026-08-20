#!/usr/bin/env python3
"""
run_grid_backtest.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
그리드 트레이딩 백테스트 실행 스크립트.

사용 예:
  # 고정 범위 (한 번 정하고 방치) — 세 가지 체결 가정을 모두 보여줌
  python run_grid_backtest.py --start 2024-01-01 --end 2024-12-31

  # 30일마다 범위 재설정 (실전 운용 방식)
  python run_grid_backtest.py --rolling 30 --start 2023-01-01

  # 범위/격자수 직접 지정
  python run_grid_backtest.py --lower 90000 --upper 120000 --grids 40

  # 선물 중립 그리드 (롱+숏), 2배 레버리지
  python run_grid_backtest.py --mode neutral --leverage 2 --rolling 30
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import argparse
import sys

import numpy as np
import pandas as pd

from ict_fvg import fetch_ohlcv, load_csv, validate_ohlcv
from grid import GridConfig, run_grid_backtest, run_rolling_grid, compare_fill_assumptions, format_report


def build_parser():
    p = argparse.ArgumentParser(
        description="그리드 트레이딩 백테스트",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    p.add_argument("--exchange", default="okx")
    p.add_argument("--symbol", default="BTC/USDT:USDT")
    p.add_argument("--market-type", default="future", choices=["future", "spot"])
    p.add_argument("--timeframe", default="5m",
                   help="잘게 쪼갤수록 체결 추정이 정확해집니다 (5m 권장)")
    p.add_argument("--start", default="2024-01-01")
    p.add_argument("--end", default=None)
    p.add_argument("--csv", default=None, help="CSV로 실행 (네트워크 미사용)")

    p.add_argument("--lower", type=float, default=None, help="격자 하단 가격")
    p.add_argument("--upper", type=float, default=None, help="격자 상단 가격")
    p.add_argument("--range-pct", type=float, default=10.0,
                   help="lower/upper 미지정 시 시작가 ±이 비율(%%)")
    p.add_argument("--grids", type=int, default=20)
    p.add_argument("--spacing", default="geometric", choices=["arithmetic", "geometric"])
    p.add_argument("--mode", default="long", choices=["long", "neutral"])

    p.add_argument("--investment", type=float, default=10_000.0)
    p.add_argument("--leverage", type=float, default=1.0)
    p.add_argument("--maker-fee", type=float, default=0.0002)
    p.add_argument("--taker-fee", type=float, default=0.0005)
    p.add_argument("--on-exit", default="hold", choices=["hold", "stop"])

    p.add_argument("--rolling", type=int, default=0,
                   help="N일마다 범위를 재설정 (0이면 고정 범위)")
    p.add_argument("--assumption", default="중립", choices=["보수", "중립", "낙관"])
    return p


def main():
    args = build_parser().parse_args()

    if args.csv:
        print(f"📂 CSV 로드: {args.csv}")
        df = load_csv(args.csv)
        if args.start:
            df = df.loc[args.start:args.end]
    else:
        df = fetch_ohlcv(exchange_id=args.exchange, symbol=args.symbol,
                         timeframe=args.timeframe, start=args.start, end=args.end,
                         market_type=args.market_type)
    validate_ohlcv(df, f"{args.timeframe} 봉")

    cfg = GridConfig(
        exchange=args.exchange, symbol=args.symbol, timeframe=args.timeframe,
        lower=args.lower, upper=args.upper, auto_range_pct=args.range_pct,
        grids=args.grids, spacing=args.spacing, mode=args.mode,
        investment=args.investment, leverage=args.leverage,
        maker_fee=args.maker_fee, taker_fee=args.taker_fee,
        on_exit=args.on_exit, fill_assumption=args.assumption,
    ).validate()

    print(f"\n⚙️  {len(df):,}봉  {df.index[0]:%Y-%m-%d} ~ {df.index[-1]:%Y-%m-%d}")

    if args.rolling > 0:
        _run_rolling(df, cfg, args)
    else:
        _run_fixed(df, cfg)
    return 0


def _run_fixed(df, cfg):
    result = run_grid_backtest(df, cfg)
    print(format_report(result))

    print("\n  체결 가정별 비교 — 이 표의 폭이 곧 '이 결과를 얼마나 믿을 수 있는가'입니다")
    print("  " + "─" * 76)
    table = compare_fill_assumptions(df, cfg)
    with pd.option_context("display.width", 200):
        print(table.to_string(index=False, float_format=lambda v: f"{v:,.1f}"))

    span = table["총수익률%"].max() - table["총수익률%"].min()
    print(f"\n  ⚠️ 가정에 따라 총수익률이 {span:.1f}%p 차이납니다.")
    if span > abs(table["총수익률%"].median()):
        print("     차이가 결과 자체보다 큽니다 — 이 백테스트로는 결론을 내릴 수 없습니다.")
    print("  " + "─" * 76)


def _run_rolling(df, cfg, args):
    result = run_rolling_grid(df, cfg, reset_days=args.rolling)
    table = result["구간별"]

    print("\n" + "━" * 78)
    print(f"  롤링 그리드 — {args.rolling}일마다 범위 재설정 (실전 운용 방식)")
    print("━" * 78)
    print(f"  격자 {cfg.grids}개, 범위 ±{cfg.auto_range_pct}%, {cfg.spacing}, "
          f"모드 {cfg.mode}, 레버리지 {cfg.leverage}배, 체결가정 {cfg.fill_assumption}")
    print("─" * 78)
    print(f"  {'구간':>4} {'시작':>12} {'체결':>7} {'실현':>9} {'평가':>9} "
          f"{'손익':>9} {'수익률%':>8} {'단순보유%':>10}")
    for i, row in table.iterrows():
        print(f"  {i + 1:>4} {row['시작']:%Y-%m-%d} {int(row['체결']):>7,} "
              f"{row['실현']:>+9,.0f} {row['평가']:>+9,.0f} {row['구간손익']:>+9,.0f} "
              f"{row['수익률%']:>+8.2f} {row['단순보유%']:>+10.1f}")

    equity = table["자본"].to_numpy()
    peak = np.maximum.accumulate(equity)
    mdd = (equity / peak - 1).min() * 100 if len(equity) else 0.0

    print("─" * 78)
    print(f"  최종 자본     : {result['최종자본']:>12,.0f}  (초기 {cfg.investment:,.0f})")
    print(f"  총수익률      : {result['총수익률']:>12.1f}%")
    print(f"  수익 구간     : {result['수익구간']:>7}/{result['구간수']}")
    print(f"  구간 최대낙폭 : {mdd:>12.1f}%")
    print(f"  최악 구간     : {table['수익률%'].min():>12.2f}%")
    print(f"  단순보유      : {result['단순보유']:>12.1f}%")
    print("─" * 78)
    print(f"  실현손익 합계 : {table['실현'].sum():>+12,.0f}")
    print(f"  평가손익 합계 : {table['평가'].sum():>+12,.0f}   ← 실현수익의 상당 부분이 여기서 반납됩니다")
    print(f"  정리 수수료   : {table['정리수수료'].sum():>12,.0f}")
    print("━" * 78)


if __name__ == "__main__":
    sys.exit(main())
