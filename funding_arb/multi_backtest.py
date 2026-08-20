# -*- coding: utf-8 -*-
"""
멀티 거래소 펀딩비 차익거래 전수 백테스트
=========================================

fetch_data.py 가 만든 통합 매트릭스(268구간 x 10거래소)를 읽어
C(10,2)=45 개 모든 거래소 페어에 대해 backtest.py 엔진을 실행하고 집계한다.

추가 분석
  --sweep      진입 임계값 x 수수료 등급 x 최대보유 민감도 분석
  --benchmark  단일 거래소 숏 + 현물 헤지(스팟-퍼프 베이시스) 벤치마크
"""

from __future__ import annotations

import argparse
import csv
import itertools
import os
import statistics as st
import sys
from typing import Dict, List

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest import Config, Row, run_backtest  # noqa: E402


# ----------------------------------------------------------------------
def load_matrix(path: str):
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    if not rows:
        raise SystemExit(f"빈 매트릭스: {path}")
    ex = [c[len("price_"):] for c in rows[0] if c.startswith("price_")]
    return rows, ex


def pair_rows(raw, a: str, b: str) -> List[Row]:
    return [Row(ts=r["ts"],
                price_a=float(r[f"price_{a}"]), price_b=float(r[f"price_{b}"]),
                funding_a=float(r[f"funding_{a}"]), funding_b=float(r[f"funding_{b}"]))
            for r in raw]


# ----------------------------------------------------------------------
def run_all(raw, ex: List[str], cfg: Config):
    out = []
    for a, b in itertools.combinations(ex, 2):
        res = run_backtest(pair_rows(raw, a, b), cfg)
        t = res.trades
        net = sum(x.net_pnl for x in t)
        wins = [x for x in t if x.net_pnl > 0]
        gl = abs(sum(x.net_pnl for x in t if x.net_pnl <= 0))
        out.append({
            "pair": f"{a}/{b}", "a": a, "b": b, "n": len(t),
            "funding": sum(x.funding_pnl for x in t),
            "fee": sum(x.fee_cost for x in t),
            "slip": sum(x.slip_cost for x in t),
            "basis": sum(x.basis_pnl for x in t),
            "net": net,
            "ret": net / cfg.capital_base * 100,
            "apr": net / cfg.capital_base * (365.0 / res.days) * 100,
            "mdd": res.mdd_pct * 100,
            "win": (len(wins) / len(t) * 100) if t else 0.0,
            "pf": (sum(x.net_pnl for x in wins) / gl) if gl > 0 else float("inf"),
            "held": sum(x.intervals for x in t),
            "days": res.days,
        })
    return out


def summarize(res: List[dict], cfg: Config, title: str, top: int = 15):
    traded = [r for r in res if r["n"] > 0]
    print("=" * 116)
    print(f"  {title}")
    print("=" * 116)
    print(f"  진입기준 |f_A-f_B| >= {cfg.entry_threshold*100:.4f}%   "
          f"청산기준 <= {cfg.exit_threshold*100:.4f}%   "
          f"최대보유 {cfg.max_hold}회차")
    print(f"  수수료 A/B {cfg.fee_a*100:.3f}%/{cfg.fee_b*100:.3f}% (편도)   "
          f"슬리피지 왕복 {cfg.slippage_round_trip*100:.3f}%   "
          f"왕복 총비용 {((cfg.fee_a+cfg.fee_b)*2+cfg.slippage_round_trip)*100:.3f}%")
    print(f"  총 페어 {len(res)}개 중 진입 발생 {len(traded)}개")
    if not traded:
        print("\n  >>> 진입 조건을 만족하는 구간이 단 한 번도 없어 거래가 발생하지 않음. <<<")
        print("=" * 116)
        return

    traded.sort(key=lambda r: r["net"], reverse=True)
    print()
    print(f"{'페어':<26} {'거래':>4} {'펀딩':>9} {'수수료':>9} {'슬립':>8} {'갭손익':>9} "
          f"{'순손익':>9} {'수익률':>8} {'APR':>8} {'MDD':>7} {'승률':>7} {'PF':>6}")
    print("-" * 116)
    for r in traded[:top]:
        pf = "inf" if r["pf"] == float("inf") else f"{r['pf']:.2f}"
        print(f"{r['pair']:<26} {r['n']:>4} {r['funding']:>9,.1f} {-r['fee']:>9,.1f} "
              f"{-r['slip']:>8,.1f} {r['basis']:>9,.1f} {r['net']:>9,.1f} "
              f"{r['ret']:>7.3f}% {r['apr']:>7.2f}% {r['mdd']:>6.3f}% {r['win']:>6.1f}% {pf:>6}")
    if len(traded) > top:
        print(f"{'... (하위 ' + str(len(traded)-top) + '개 생략)':<26}")
    print("-" * 116)

    nets = [r["net"] for r in traded]
    rets = [r["ret"] for r in traded]
    prof = [r for r in traded if r["net"] > 0]
    alln = sum(r["n"] for r in traded)
    allw = sum(r["win"] / 100 * r["n"] for r in traded)
    print(f"  흑자 페어      : {len(prof)}/{len(traded)} ({len(prof)/len(traded)*100:.1f}%)")
    print(f"  페어 순수익률  : 평균 {st.mean(rets):+.4f}%  중앙 {st.median(rets):+.4f}%  "
          f"최고 {max(rets):+.4f}%  최저 {min(rets):+.4f}%")
    print(f"  총 거래 수     : {alln}건   전체 승률 {allw/alln*100 if alln else 0:.1f}%")
    print(f"  총 순손익 합계 : {sum(nets):+,.1f} USD (페어당 명목 ${cfg.notional:,.0f} 가정)")
    print(f"  평균 MDD       : {st.mean([r['mdd'] for r in traded]):.4f}%")
    print("=" * 116)


# ----------------------------------------------------------------------
def sweep(raw, ex, base: Config):
    print("=" * 116)
    print("  민감도 분석 : 진입 임계값 x 수수료 등급 x 최대 보유 회차")
    print("=" * 116)
    fee_tiers = [("테이커 0.050%", 0.0005, 0.0004),
                 ("테이커 0.020%", 0.0002, 0.0002),
                 ("메이커 0.010%", 0.0001, 0.0001),
                 ("메이커 0.000%", 0.00000, 0.0001)]
    thresholds = [0.0015, 0.0010, 0.0005, 0.0003, 0.0002, 0.0001, 0.00005]
    holds = [9, 27, 276]

    print(f"\n{'임계값':>9} {'최대보유':>7} {'수수료등급':<14} {'거래페어':>7} {'총거래':>7} "
          f"{'평균수익률':>10} {'최고':>9} {'흑자페어':>8} {'전체승률':>8}")
    print("-" * 116)
    for th in thresholds:
        for hold in holds:
            for label, fee, slip in fee_tiers:
                cfg = Config(notional=base.notional, fee_a=fee, fee_b=fee,
                             slippage_round_trip=slip, entry_threshold=th,
                             exit_threshold=th * 0.33, max_hold=hold)
                res = run_all(raw, ex, cfg)
                tr = [r for r in res if r["n"] > 0]
                if not tr:
                    if label == fee_tiers[0][0] and hold == holds[0]:
                        print(f"{th*100:>8.4f}% {'-':>7} {'(전 등급)':<14} "
                              f"{0:>7} {0:>7} {'거래 없음':>10}")
                    continue
                rets = [r["ret"] for r in tr]
                alln = sum(r["n"] for r in tr)
                allw = sum(r["win"] / 100 * r["n"] for r in tr)
                prof = sum(1 for r in tr if r["net"] > 0)
                print(f"{th*100:>8.4f}% {hold:>7} {label:<14} {len(tr):>7} {alln:>7} "
                      f"{st.mean(rets):>+9.4f}% {max(rets):>+8.4f}% "
                      f"{prof:>7}개 {allw/alln*100:>7.1f}%")
            print()


# ----------------------------------------------------------------------
def benchmark(raw, ex, cfg: Config):
    """단일 거래소 퍼프 숏 + 무비용 현물 롱 헤지(스팟-퍼프) 벤치마크.

    펀딩 '격차'가 아니라 펀딩 '전액'을 수령하는 구조.
    현물 다리는 펀딩 0, 수수료는 퍼프와 동일하다고 가정(보수적).
    """
    print("=" * 116)
    print("  [벤치마크] 단일 거래소 퍼프 숏 + 현물 롱 (스팟-퍼프 베이시스, 격차가 아닌 펀딩 전액 수령)")
    print("=" * 116)
    n = len(raw)
    rt = (cfg.fee_a + cfg.fee_b) * 2 + cfg.slippage_round_trip
    days = n * 8 / 24.0
    print(f"  가정: 3개월 1회 진입 후 만기 보유(왕복 총비용 {rt*100:.3f}% 1회만 부담),"
          f" 현물 헤지 완전(갭손익 0)\n")
    print(f"{'거래소':<14} {'누적펀딩':>10} {'왕복비용':>9} {'순수익':>10} {'순수익률':>10} "
          f"{'APR':>9} {'음수구간':>9}")
    print("-" * 116)
    out = []
    for e in ex:
        f = [float(r[f"funding_{e}"]) for r in raw]
        gross = sum(f) * cfg.notional
        cost = rt * cfg.notional
        net = gross - cost
        neg = sum(1 for x in f if x < 0) / len(f) * 100
        out.append((net, e, gross, cost, neg))
    out.sort(reverse=True)
    for net, e, gross, cost, neg in out:
        print(f"{e:<14} {gross:>10,.2f} {-cost:>9,.2f} {net:>10,.2f} "
              f"{net/cfg.capital_base*100:>9.3f}% {net/cfg.capital_base*(365/days)*100:>8.2f}% "
              f"{neg:>8.1f}%")
    print("-" * 116)
    print(f"  * 기간 {days:.0f}일 ({n}구간). 현물 매수 자금이 별도로 필요하므로"
          f" 실제 자기자본 기준 수익률은 위 값의 약 1/2.")
    print("=" * 116)


# ----------------------------------------------------------------------
def structural(raw, ex, cfg: Config, lookback: int = 21):
    """구조적 장기보유 모드.

    펀딩 격차가 '스파이크'가 아니라 '지속적 드리프트' 라는 관측에 근거해,
    임계값 트리거 대신 lookback 구간 이동평균 격차의 부호로 방향을 정해
    1회 진입 후 데이터 종료까지 보유한다. 왕복 비용은 단 1회만 부담.
    """
    print("=" * 116)
    print(f"  [대안 전략] 구조적 장기보유 : {lookback}회차 이동평균 격차 방향으로 1회 진입 후 만기 보유")
    print("=" * 116)
    rt = (cfg.fee_a + cfg.fee_b) * 2 + cfg.slippage_round_trip
    n = len(raw)
    days = n * 8 / 24.0
    print(f"  왕복 총비용 {rt*100:.3f}% (전 기간 1회) | 진입 시점 = {lookback}회차 경과 후 | "
          f"보유 {n-lookback}회차\n")
    print(f"{'페어':<26} {'방향':<10} {'누적펀딩':>10} {'비용':>8} {'갭손익':>9} "
          f"{'순손익':>9} {'수익률':>8} {'APR':>8} {'MDD':>8}")
    print("-" * 116)
    out = []
    for a, b in itertools.combinations(ex, 2):
        rows = pair_rows(raw, a, b)
        avg = st.mean(r.f_spread for r in rows[:lookback])
        if avg == 0:
            continue
        dir_a = -1 if avg > 0 else 1          # 격차 양수면 A 숏
        e0 = rows[lookback]
        qty = cfg.notional / e0.mid
        cost = (qty * e0.price_a * cfg.fee_a + qty * e0.price_b * cfg.fee_b
                + cfg.slippage_round_trip / 2 * qty * e0.mid)
        last = rows[-1]
        cost += (qty * last.price_a * cfg.fee_a + qty * last.price_b * cfg.fee_b
                 + cfg.slippage_round_trip / 2 * qty * last.mid)

        eq, peak, mdd, fund = -0.0, 0.0, 0.0, 0.0
        eq = -(qty * e0.price_a * cfg.fee_a + qty * e0.price_b * cfg.fee_b
               + cfg.slippage_round_trip / 2 * qty * e0.mid)
        for i in range(lookback + 1, n):
            fund += cfg.notional * (-dir_a * rows[i].f_spread)
            basis = qty * dir_a * (rows[i].basis - rows[lookback].basis)
            eq_i = eq + fund + basis
            peak = max(peak, eq_i)
            mdd = min(mdd, eq_i - peak)
        basis_total = qty * dir_a * (last.basis - e0.basis)
        net = fund - cost + basis_total
        out.append((net, f"{a}/{b}", "A숏/B롱" if dir_a == -1 else "A롱/B숏",
                    fund, cost, basis_total, abs(mdd)))
    out.sort(reverse=True)
    for net, pair, side, fund, cost, basis, mdd in out[:15]:
        print(f"{pair:<26} {side:<10} {fund:>10,.2f} {-cost:>8,.2f} {basis:>9,.2f} "
              f"{net:>9,.2f} {net/cfg.capital_base*100:>7.3f}% "
              f"{net/cfg.capital_base*(365/days)*100:>7.2f}% {mdd/cfg.capital_base*100:>7.3f}%")
    print("-" * 116)
    prof = sum(1 for o in out if o[0] > 0)
    rets = [o[0] / cfg.capital_base * 100 for o in out]
    print(f"  흑자 페어 : {prof}/{len(out)} ({prof/len(out)*100:.1f}%)")
    print(f"  수익률    : 평균 {st.mean(rets):+.4f}%  중앙 {st.median(rets):+.4f}%  "
          f"최고 {max(rets):+.4f}%  최저 {min(rets):+.4f}%")
    print(f"  연환산    : 평균 {st.mean(rets)*365/days:+.2f}%  최고 {max(rets)*365/days:+.2f}%")
    print("=" * 116)


# ----------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data", default="funding_arb/data/matrix.csv")
    p.add_argument("--notional", type=float, default=10_000.0)
    p.add_argument("--fee", type=float, default=0.0005)
    p.add_argument("--slippage", type=float, default=0.0004)
    p.add_argument("--entry-threshold", type=float, default=0.0015)
    p.add_argument("--exit-threshold", type=float, default=0.0005)
    p.add_argument("--max-hold", type=int, default=9)
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--sweep", action="store_true")
    p.add_argument("--benchmark", action="store_true")
    p.add_argument("--structural", action="store_true")
    p.add_argument("--lookback", type=int, default=21)
    a = p.parse_args()

    raw, ex = load_matrix(a.data)
    cfg = Config(notional=a.notional, fee_a=a.fee, fee_b=a.fee,
                 slippage_round_trip=a.slippage,
                 entry_threshold=a.entry_threshold,
                 exit_threshold=a.exit_threshold, max_hold=a.max_hold)

    print(f"\n데이터: {a.data}")
    print(f"기간  : {raw[0]['ts']} ~ {raw[-1]['ts']}  ({len(raw)}구간, {len(raw)*8/24:.0f}일)")
    print(f"거래소: {len(ex)}개 - {', '.join(ex)}")
    print(f"페어  : {len(list(itertools.combinations(ex, 2)))}개\n")

    summarize(run_all(raw, ex, cfg), cfg,
              "[본 백테스트] 요청 기준 (진입 0.15% / 테이커 0.05% / 슬리피지 0.04%)", a.top)
    if a.sweep:
        print()
        sweep(raw, ex, cfg)
    if a.structural:
        print()
        structural(raw, ex, cfg, a.lookback)
    if a.benchmark:
        print()
        benchmark(raw, ex, cfg)


if __name__ == "__main__":
    main()
