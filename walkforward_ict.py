#!/usr/bin/env python3
"""
walkforward_ict.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
앵커드 워크포워드 검증 (Anchored Walk-Forward Optimization).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
학습/검증 1회 분할로는 부족한 이유
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
기간을 한 번만 둘로 잘라 검증하면, 그 검증 구간이 우연히 유리한
장세였을 수 있습니다. 분할 지점을 조금만 옮겨도 결론이 뒤집힙니다.

워크포워드는 이렇게 합니다:

    구간1   [── 학습 ──][검증1]
    구간2   [──── 학습 ────][검증2]
    구간3   [────── 학습 ──────][검증3]
    ...

각 구간마다 '그 시점까지의 데이터만' 보고 최적 파라미터를 고른 뒤,
그 다음 구간에서 성과를 측정합니다. 그리고 검증 구간들만 이어 붙입니다.

이렇게 나온 곡선이 "실제로 주기적으로 재최적화하며 운용했다면
얻었을 성과"에 가장 가깝습니다. 미래를 본 적이 한 번도 없기 때문입니다.

⚠️ 이 결과가 단일 분할보다 나쁘게 나오는 게 정상입니다.
   단일 분할이 좋아 보였다면 그건 대체로 운이었다는 뜻입니다.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

사용 예:
  python walkforward_ict.py --symbol BTC/USDT:USDT --folds 5
  python walkforward_ict.py --symbol ETH/USDT:USDT --folds 5 --workers 4
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import argparse
import json
import os
import sys
import time

import numpy as np
import pandas as pd

from ict_fvg import StrategyConfig, run_backtest
from optimize_ict import (
    apply_params, evaluate, expand_grid, load_pair, search_group, slice_period,
)


# 워크포워드용 그리드 — 각 구간마다 이 조합을 전부 돌리므로 너무 크면 안 된다.
WF_GRID = {
    "tf_pair": [("4h", "1h"), ("1h", "15m")],
    "min_rr": [1.5, 2.0, 3.0],
    "sl_mode": ["swing", "wider"],
    "zone_mode": ["ce", "full"],
    "require_sweep": [True, False],
    "require_mss": [True, False],
    "min_fvg_atr": [0.15, 0.30],
    "tp_target": ["nearest", "range_high"],
    "fvg_max_age_bars": [40, 80],
}


def make_folds(start, end, folds: int, min_train_ratio: float = 0.4):
    """전체 기간을 검증 구간 N개로 나눈다.

    학습 구간은 항상 '처음부터 그 검증 구간 직전까지'(앵커드)이므로,
    뒤로 갈수록 학습 데이터가 늘어난다. 실제 운용 방식과 같다.
    """
    start = pd.Timestamp(start, tz="UTC")
    end = pd.Timestamp(end, tz="UTC")
    total = end - start

    first_test = start + total * min_train_ratio
    test_span = (end - first_test) / folds

    result = []
    for k in range(folds):
        test_start = first_test + test_span * k
        test_end = first_test + test_span * (k + 1)
        result.append({
            "학습": (start.strftime("%Y-%m-%d"), test_start.strftime("%Y-%m-%d")),
            "검증": (test_start.strftime("%Y-%m-%d"), test_end.strftime("%Y-%m-%d")),
        })
    return result


def pick_best(combos, args, base, train_range):
    """학습 구간에서 최고 조합을 고른다. 검증 구간은 절대 보지 않는다."""
    grouped = {}
    for combo in combos:
        grouped.setdefault(tuple(combo["tf_pair"]), []).append(combo)

    rows = []
    for (htf_tf, ltf_tf), group in grouped.items():
        ltf_full, htf_full = load_pair(args.symbol, args.exchange, htf_tf, ltf_tf,
                                       args.start, args.end, args.market_type)
        ltf_tr, htf_tr = slice_period(ltf_full, htf_full, ltf_tf, htf_tf, *train_range)
        if ltf_tr is None or len(ltf_tr) < 500:
            continue
        group_base = apply_params(base, {"tf_pair": (htf_tf, ltf_tf)})
        result = search_group(group, ltf_tr, htf_tr, group_base,
                              args.min_trades, args.workers, args.min_stop_pct)
        for row, combo in zip(result, group):
            row["_params"] = combo
        rows.extend(result)

    valid = [r for r in rows if np.isfinite(r["t통계량"])]
    if not valid:
        return None
    return max(valid, key=lambda r: r["t통계량"])


def run_walkforward(args):
    base = StrategyConfig(
        exchange=args.exchange, symbol=args.symbol, market_type=args.market_type,
        initial_capital=args.capital, risk_pct=args.risk_pct,
    )
    combos = expand_grid(WF_GRID)
    folds = make_folds(args.start, args.end, args.folds, args.min_train_ratio)

    print("━" * 78)
    print("  앵커드 워크포워드 검증")
    print("━" * 78)
    print(f"  종목      : {args.exchange} {args.symbol}")
    print(f"  전체 기간 : {args.start} ~ {args.end}")
    print(f"  구간 수   : {args.folds}   구간당 조합 {len(combos):,}개")
    print(f"  최소 거래 : {args.min_trades}건   최소 손절폭 : {args.min_stop_pct}%")

    records = []
    started = time.time()

    for k, fold in enumerate(folds):
        train_range, test_range = fold["학습"], fold["검증"]
        print(f"\n── 구간 {k + 1}/{args.folds} ─────────────────────────────")
        print(f"   학습 {train_range[0]} ~ {train_range[1]}  →  검증 {test_range[0]} ~ {test_range[1]}")

        best = pick_best(combos, args, base, train_range)
        if best is None:
            print("   ⚠️ 학습 구간에서 조건을 만족하는 조합이 없음 — 이 구간 건너뜀")
            records.append({"구간": k + 1, "검증시작": test_range[0], "상태": "조합없음",
                            "거래수": 0, "수익률": 0.0})
            continue

        params = best["_params"]
        htf_tf, ltf_tf = params["tf_pair"]
        cfg = apply_params(base, params)

        ltf_full, htf_full = load_pair(args.symbol, args.exchange, htf_tf, ltf_tf,
                                       args.start, args.end, args.market_type)
        ltf_te, htf_te = slice_period(ltf_full, htf_full, ltf_tf, htf_tf, *test_range)
        test = evaluate(cfg, ltf_te, htf_te, min_trades=1)

        chosen = "  ".join(f"{key}={value}" for key, value in params.items())
        print(f"   선택된 설정: {chosen}")
        print(f"   학습 성과  : t={best['t통계량']:.2f}  거래 {best['거래수']}건  "
              f"기대값 {best['기대값R']:.2f}R  수익률 {best['수익률']:+.1f}%")
        print(f"   ▶ 검증 성과: 거래 {test['거래수']}건  기대값 {test['기대값R']:+.2f}R  "
              f"수익률 {test['수익률']:+.1f}%  MDD {test['MDD']:.1f}%  PF {test['PF']:.2f}")

        # 같은 구간의 단순보유 수익률 (기준선)
        segment = ltf_te["close"]
        buy_hold = (segment.iloc[-1] / segment.iloc[0] - 1.0) * 100.0
        print(f"     (같은 구간 단순보유: {buy_hold:+.1f}%)")

        records.append({
            "구간": k + 1,
            "검증시작": test_range[0],
            "검증종료": test_range[1],
            "학습_t": best["t통계량"],
            "학습_거래수": best["거래수"],
            "거래수": test["거래수"],
            "기대값R": test["기대값R"],
            "수익률": test["수익률"],
            "MDD": test["MDD"],
            "PF": test["PF"],
            "승률": test["승률"],
            "단순보유": buy_hold,
            "설정": json.dumps(params, ensure_ascii=False),
        })

    print(f"\n총 소요 {time.time() - started:.0f}초")
    return pd.DataFrame(records)


def print_summary(df: pd.DataFrame, capital: float):
    """검증 구간들만 이어 붙인 최종 성과."""
    traded = df[df.get("거래수", 0) > 0]

    print("\n" + "━" * 78)
    print("  워크포워드 종합 — 검증 구간만 이어 붙인 결과")
    print("━" * 78)
    print(f"  {'구간':>4} {'검증시작':>12} {'거래':>5} {'기대값R':>8} {'수익률%':>9} "
          f"{'MDD%':>7} {'PF':>6} {'단순보유%':>10}")
    print("─" * 78)

    equity = capital
    for _, row in df.iterrows():
        if row.get("거래수", 0) == 0:
            print(f"  {int(row['구간']):>4} {row['검증시작']:>12} {'—':>5} {'—':>8} "
                  f"{'—':>9} {'—':>7} {'—':>6} {'—':>10}")
            continue
        equity *= (1 + row["수익률"] / 100.0)
        print(f"  {int(row['구간']):>4} {row['검증시작']:>12} {int(row['거래수']):>5} "
              f"{row['기대값R']:>8.2f} {row['수익률']:>9.1f} {row['MDD']:>7.1f} "
              f"{row['PF']:>6.2f} {row['단순보유']:>10.1f}")

    print("─" * 78)
    if traded.empty:
        print("  거래가 발생한 구간이 없습니다.")
        print("━" * 78)
        return

    total_return = (equity / capital - 1.0) * 100.0
    positive = int((traded["수익률"] > 0).sum())
    print(f"  최종 자본     : {equity:,.0f}  (초기 {capital:,.0f})")
    print(f"  누적 수익률   : {total_return:+.1f}%")
    print(f"  총 거래 수    : {int(traded['거래수'].sum()):,}건")
    print(f"  평균 기대값   : {traded['기대값R'].mean():+.2f}R")
    print(f"  수익 구간     : {positive}/{len(traded)}  "
          f"({positive / len(traded) * 100:.0f}%)")
    print(f"  최악 구간     : {traded['수익률'].min():+.1f}%")
    print(f"  구간 최대 MDD : {traded['MDD'].min():.1f}%")
    print(f"  단순보유 누적 : {(np.prod(1 + traded['단순보유'] / 100.0) - 1) * 100:+.1f}%")
    print("━" * 78)
    print("  ⚠️ 이 수치조차 상한선입니다. 지정가 미체결과 자금조달 수수료는 빠져 있습니다.")
    print("━" * 78)


def build_parser():
    parser = argparse.ArgumentParser(description="ICT FVG 앵커드 워크포워드 검증")
    parser.add_argument("--exchange", default="okx")
    parser.add_argument("--symbol", default="BTC/USDT:USDT")
    parser.add_argument("--market-type", default="future", choices=["future", "spot"])
    parser.add_argument("--start", default="2023-01-01")
    parser.add_argument("--end", default="2026-08-15")
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--min-train-ratio", type=float, default=0.4,
                        help="첫 검증 구간이 시작되는 지점 (앞쪽은 전부 학습용)")
    parser.add_argument("--min-trades", type=int, default=25)
    parser.add_argument("--min-stop-pct", type=float, default=0.30)
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    parser.add_argument("--capital", type=float, default=10_000.0)
    parser.add_argument("--risk-pct", type=float, default=1.0)
    parser.add_argument("--out-dir", default="optimize_results")
    return parser


def main():
    args = build_parser().parse_args()
    df = run_walkforward(args)
    print_summary(df, args.capital)

    os.makedirs(args.out_dir, exist_ok=True)
    tag = args.symbol.replace("/", "").replace(":", "")
    path = os.path.join(args.out_dir, f"walkforward_{tag}.csv")
    df.to_csv(path, index=False)
    print(f"\n💾 저장: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
