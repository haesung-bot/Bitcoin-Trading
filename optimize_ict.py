#!/usr/bin/env python3
"""
optimize_ict.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ICT FVG 전략 파라미터 탐색 (In-Sample / Out-of-Sample 분리).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ 왜 그냥 '제일 좋은 값'을 뽑으면 안 되는가
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
파라미터 조합 500개를 돌리면, 그중 하나는 반드시 좋아 보입니다.
동전 500개를 10번씩 던지면 그중 하나는 8번 앞면이 나오는 것과 같습니다.
그 동전이 특별한 게 아니듯, 그 파라미터도 특별하지 않습니다.

같은 데이터에서 최적값을 찾고 그 데이터로 성과를 보고하면
그건 '전략 성과'가 아니라 '데이터를 외운 결과'입니다.

그래서 이 스크립트는:
  1. 기간을 학습(IS) / 검증(OOS)으로 나눕니다
  2. 최적화는 학습 구간에서만 합니다
  3. 성과 보고는 한 번도 보지 않은 검증 구간으로 합니다
  4. 학습에서 좋았는데 검증에서 무너지면 → 그건 과최적화입니다

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
순위 기준: 수익률이 아니라 t-통계량
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
수익률로 줄을 세우면 '거래 3번 해서 크게 먹은 운 좋은 조합'이 1등을 합니다.
그래서 거래당 R배수의 t-통계량으로 순위를 매깁니다.

    t = 평균(R) / 표준편차(R) × √거래수

이 값은 "이 우위가 우연일 확률"을 반영합니다. 거래 수가 적으면
자동으로 낮아지기 때문에, 표본이 부족한 조합이 1등을 차지하지 못합니다.
경험적으로 t < 2 면 통계적으로 우연과 구분되지 않습니다.

사용 예:
  python optimize_ict.py --symbol BTC/USDT:USDT --grid coarse
  python optimize_ict.py --symbol BTC/USDT:USDT --grid fine --top 20
  python optimize_ict.py --verify-config best_config.json --symbol ETH/USDT:USDT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import argparse
import itertools
import json
import math
import os
import sys
import time
from multiprocessing import Pool

import numpy as np
import pandas as pd

from ict_fvg import StrategyConfig, fetch_ohlcv, run_backtest
from ict_fvg.config import timeframe_ms


# ══════════════════════════════════════════════════════════════════
#  파라미터 그리드
# ══════════════════════════════════════════════════════════════════

GRIDS = {
    # 1단계: 영향이 큰 파라미터만 넓게 훑는다
    "coarse": {
        "tf_pair": [("1h", "15m"), ("4h", "15m"), ("4h", "1h"), ("1h", "5m")],
        "min_rr": [1.5, 2.0, 3.0],
        "sl_mode": ["fvg", "swing", "wider"],
        "zone_mode": ["ce", "full"],
        "require_mss": [True, False],
        "min_fvg_atr": [0.0, 0.15, 0.30],
        "tp_target": ["nearest", "range_high"],
    },
    # 2단계: 1단계에서 좋았던 영역 주변의 세부 파라미터
    "fine": {
        "require_sweep": [True, False],
        "setup_valid_bars": [20, 40, 80],
        "fvg_max_age_bars": [40, 80, 160],
        "fvg_pre_mss_bars": [0, 3, 6],
        "ltf_swing": [(2, 2), (3, 3)],
        "htf_swing": [(2, 2), (3, 3), (5, 5)],
        "sl_buffer_atr": [0.0, 0.10, 0.25],
    },
}


def expand_grid(grid: dict) -> list:
    """dict of lists → 조합 리스트."""
    keys = list(grid)
    return [dict(zip(keys, values)) for values in itertools.product(*(grid[k] for k in keys))]


def apply_params(base: StrategyConfig, params: dict) -> StrategyConfig:
    """탐색용 파라미터 dict를 StrategyConfig에 적용한다."""
    from dataclasses import replace

    kwargs = {}
    for key, value in params.items():
        if key == "tf_pair":
            kwargs["htf"], kwargs["ltf"] = value
        elif key == "ltf_swing":
            kwargs["ltf_swing_left"], kwargs["ltf_swing_right"] = value
        elif key == "htf_swing":
            kwargs["htf_swing_left"], kwargs["htf_swing_right"] = value
        else:
            kwargs[key] = value
    return replace(base, **kwargs)


# ══════════════════════════════════════════════════════════════════
#  데이터 준비
# ══════════════════════════════════════════════════════════════════

def load_pair(symbol, exchange, htf, ltf, start, end, market_type="future"):
    """(HTF, LTF) 한 쌍을 받아온다. 캐시가 있으면 네트워크를 타지 않는다."""
    common = dict(exchange_id=exchange, symbol=symbol, start=start, end=end,
                  market_type=market_type, verbose=False)
    return fetch_ohlcv(timeframe=ltf, **common), fetch_ohlcv(timeframe=htf, **common)


def slice_period(ltf, htf, ltf_tf, htf_tf, start, end):
    """기간을 잘라낸다.

    LTF는 [start, end] 구간만 쓰고, HTF는 그 이전 이력까지 전부 준다.
    실전에서도 HTF 과거 이력은 항상 가지고 있으므로 이게 현실적이다.
    다만 LTF 마지막 봉이 마감된 시점 이후에 닫히는 HTF 봉은 제외한다
    (아직 존재하지 않는 정보이므로).
    """
    ltf_slice = ltf.loc[start:end]
    if len(ltf_slice) == 0:
        return None, None

    ltf_end = ltf_slice.index[-1] + pd.Timedelta(milliseconds=timeframe_ms(ltf_tf))
    htf_close = htf.index + pd.Timedelta(milliseconds=timeframe_ms(htf_tf))
    htf_slice = htf[htf_close <= ltf_end]
    return ltf_slice, htf_slice


# ══════════════════════════════════════════════════════════════════
#  평가
# ══════════════════════════════════════════════════════════════════

def t_statistic(r_multiples: pd.Series) -> float:
    """거래당 R배수의 t-통계량. 우위가 우연인지 판단하는 척도."""
    n = len(r_multiples)
    if n < 2:
        return 0.0
    std = r_multiples.std(ddof=1)
    if not np.isfinite(std) or std <= 0:
        return 0.0
    return float(r_multiples.mean() / std * math.sqrt(n))


def evaluate(cfg, ltf, htf, min_trades: int, min_stop_pct: float = 0.0) -> dict:
    """설정 하나를 평가해 요약 지표를 돌려준다."""
    try:
        result = run_backtest(ltf, htf, cfg)
    except Exception as exc:  # 파라미터 조합이 유효하지 않은 경우
        return {"오류": str(exc)[:80], "거래수": 0, "t통계량": float("-inf"),
                "t_raw": 0.0, "손절폭%": 0.0, "손익비중앙": 0.0}

    trades = result.trades
    metrics = result.metrics
    n = len(trades)

    t_stat = t_statistic(trades["r_multiple"]) if n else 0.0

    # ⚠️ 손절폭이 지나치게 좁은 조합을 걸러낸다.
    #    손절폭이 가격의 0.2%밖에 안 되면 R배수가 그 미세한 값 기준으로 계산돼
    #    t-통계량이 부풀려지고, 수량은 레버리지 상한에 걸려 실제 리스크가 의도보다
    #    작아진다. 게다가 그런 구간은 슬리피지/스프레드가 리스크의 상당 부분을
    #    차지해서 백테스트를 가장 믿기 어렵다.
    if n:
        stop_pct = ((trades["entry_price"] - trades["stop_price"]).abs()
                    / trades["entry_price"] * 100.0)
        median_stop_pct = float(stop_pct.median())
        median_rr = float(trades["planned_rr"].median())
    else:
        median_stop_pct = median_rr = 0.0

    # 표본이 부족하거나 손절폭이 비현실적인 조합은 순위에서 제외한다
    disqualified = n < min_trades or median_stop_pct < min_stop_pct
    score = float("-inf") if disqualified else t_stat

    profit_factor = metrics["손익비(PF)"]
    return {
        "거래수": n,
        "t통계량": score,
        "t_raw": t_stat,
        "손절폭%": median_stop_pct,
        "손익비중앙": median_rr,
        "기대값R": metrics["기대값(R)"],
        "승률": metrics["승률(%)"],
        "PF": profit_factor if np.isfinite(profit_factor) else 99.0,
        "수익률": metrics["총수익률(%)"],
        "MDD": metrics["최대낙폭(%)"],
        "CAGR": metrics["CAGR(%)"],
        "최대연속손실": metrics["최대연속손실"],
    }


# 워커 프로세스가 fork로 상속받는 데이터 (Linux 전용 최적화)
_WORKER_DATA = {}


def _worker(job):
    """멀티프로세싱 워커. 조합 하나를 평가한다."""
    params, base_dict, min_trades, min_stop_pct = job
    base = StrategyConfig(**base_dict)
    cfg = apply_params(base, params)
    ltf, htf = _WORKER_DATA["ltf"], _WORKER_DATA["htf"]
    row = evaluate(cfg, ltf, htf, min_trades, min_stop_pct)
    row.update({k: (list(v) if isinstance(v, tuple) else v) for k, v in params.items()})
    return row


def search_group(params_list, ltf, htf, base: StrategyConfig, min_trades, workers, min_stop_pct):
    """같은 (HTF, LTF) 데이터를 쓰는 조합들을 한꺼번에 평가한다."""
    global _WORKER_DATA
    _WORKER_DATA = {"ltf": ltf, "htf": htf}

    base_dict = base.to_dict()
    jobs = [(p, base_dict, min_trades, min_stop_pct) for p in params_list]

    if workers <= 1:
        return [_worker(job) for job in jobs]

    # fork 방식이라 워커가 _WORKER_DATA를 그대로 물려받는다 (데이터 복사 전송 불필요)
    with Pool(workers) as pool:
        return pool.map(_worker, jobs, chunksize=1)


# ══════════════════════════════════════════════════════════════════
#  메인 흐름
# ══════════════════════════════════════════════════════════════════

def run_search(args, base: StrategyConfig, grid: dict, is_range, oos_range):
    """학습 구간에서 그리드 탐색 → 상위 조합을 검증 구간에서 재평가."""
    combos = expand_grid(grid)
    print(f"\n🔍 탐색할 조합: {len(combos):,}개  (워커 {args.workers}개)")

    # (HTF, LTF)별로 묶어서 데이터를 한 번만 읽는다
    grouped = {}
    for combo in combos:
        pair = tuple(combo.get("tf_pair", (base.htf, base.ltf)))
        grouped.setdefault(pair, []).append(combo)

    is_rows = []
    started = time.time()

    for (htf_tf, ltf_tf), group in grouped.items():
        print(f"\n  ── {htf_tf}/{ltf_tf} : {len(group):,}개 조합 …", flush=True)
        ltf_full, htf_full = load_pair(args.symbol, args.exchange, htf_tf, ltf_tf,
                                       args.start, args.end, args.market_type)
        ltf_is, htf_is = slice_period(ltf_full, htf_full, ltf_tf, htf_tf, *is_range)
        if ltf_is is None or len(ltf_is) < 1000:
            print("     데이터 부족 — 건너뜀")
            continue

        group_base = apply_params(base, {"tf_pair": (htf_tf, ltf_tf)})
        rows = search_group(group, ltf_is, htf_is, group_base, args.min_trades,
                            args.workers, args.min_stop_pct)
        for row in rows:
            row.setdefault("tf_pair", [htf_tf, ltf_tf])
        is_rows.extend(rows)
        print(f"     완료 ({time.time() - started:.0f}초 경과)", flush=True)

    is_df = pd.DataFrame(is_rows)
    is_df = is_df[np.isfinite(is_df["t통계량"])].sort_values("t통계량", ascending=False)

    if is_df.empty:
        print(f"\n❌ 최소 거래수({args.min_trades}건)를 넘긴 조합이 없습니다. "
              f"--min-trades를 낮추거나 기간을 늘리세요.")
        return None, None

    print(f"\n✅ 학습 구간 탐색 완료 — 유효 조합 {len(is_df):,}개 "
          f"({time.time() - started:.0f}초)")

    # ── 상위 조합을 검증 구간에서 재평가 ──
    top = is_df.head(args.top).reset_index(drop=True)
    print(f"\n🔬 상위 {len(top)}개를 검증 구간에서 재평가합니다 "
          f"(이 데이터는 최적화에 쓰이지 않았습니다)")

    param_keys = [k for k in grid]
    oos_rows = []
    for _, row in top.iterrows():
        params = {k: (tuple(row[k]) if isinstance(row[k], list) else row[k])
                  for k in param_keys}
        htf_tf, ltf_tf = params.get("tf_pair", (base.htf, base.ltf))
        ltf_full, htf_full = load_pair(args.symbol, args.exchange, htf_tf, ltf_tf,
                                       args.start, args.end, args.market_type)
        ltf_oos, htf_oos = slice_period(ltf_full, htf_full, ltf_tf, htf_tf, *oos_range)
        cfg = apply_params(base, params)
        oos_rows.append(evaluate(cfg, ltf_oos, htf_oos, min_trades=1))

    oos_df = pd.DataFrame(oos_rows)
    return top, oos_df


def print_comparison(top, oos_df, param_keys, is_range, oos_range):
    """학습 성과와 검증 성과를 나란히 보여준다."""
    print("\n" + "━" * 108)
    print("  학습(IS) vs 검증(OOS) 비교 — IS에서 좋았는데 OOS에서 무너지면 과최적화입니다")
    print(f"  IS  : {is_range[0]} ~ {is_range[1]}")
    print(f"  OOS : {oos_range[0]} ~ {oos_range[1]}")
    print("━" * 122)
    header = (f"{'#':>2}  {'IS_t':>6} {'IS_거래':>6} {'IS_R':>6} {'IS_수익%':>8} "
              f"{'손절%':>6} {'RR중앙':>6} │ "
              f"{'OOS_t':>6} {'OOS_거래':>7} {'OOS_R':>6} {'OOS_수익%':>9} {'OOS_MDD%':>8} {'OOS_PF':>6}")
    print(header)
    print("─" * 122)

    for i in range(len(top)):
        is_row, oos_row = top.iloc[i], oos_df.iloc[i]
        print(f"{i:>2}  {is_row['t통계량']:>6.2f} {is_row['거래수']:>6} "
              f"{is_row['기대값R']:>6.2f} {is_row['수익률']:>8.1f} "
              f"{is_row['손절폭%']:>6.2f} {is_row['손익비중앙']:>6.1f} │ "
              f"{oos_row['t_raw']:>6.2f} {oos_row['거래수']:>7} "
              f"{oos_row['기대값R']:>6.2f} {oos_row['수익률']:>9.1f} "
              f"{oos_row['MDD']:>8.1f} {oos_row['PF']:>6.2f}")

    print("─" * 122)
    print("  파라미터")
    for i in range(len(top)):
        params = {k: top.iloc[i][k] for k in param_keys}
        print(f"   #{i}: " + "  ".join(f"{k}={v}" for k, v in params.items()))
    print("━" * 122)


def build_parser():
    parser = argparse.ArgumentParser(
        description="ICT FVG 전략 파라미터 탐색 (IS/OOS 분리)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--exchange", default="okx")
    parser.add_argument("--symbol", default="BTC/USDT:USDT")
    parser.add_argument("--market-type", default="future", choices=["future", "spot"])
    parser.add_argument("--start", default="2023-01-01")
    parser.add_argument("--end", default="2026-08-15")
    parser.add_argument("--split", type=float, default=0.6,
                        help="학습 구간 비율 (나머지가 검증 구간)")
    parser.add_argument("--grid", default="coarse", choices=list(GRIDS),
                        help="사용할 파라미터 그리드")
    parser.add_argument("--min-trades", type=int, default=30,
                        help="학습 구간 최소 거래 수 (미만이면 순위에서 제외)")
    parser.add_argument("--min-stop-pct", type=float, default=0.30,
                        help="손절폭 중앙값 하한(가격 대비 %%). 이보다 좁으면 순위에서 제외. "
                             "손절폭이 지나치게 좁으면 R배수와 t통계량이 부풀려지고 "
                             "슬리피지 영향이 커져 백테스트를 믿기 어렵다.")
    parser.add_argument("--top", type=int, default=15,
                        help="검증 구간에서 재평가할 상위 조합 수")
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    parser.add_argument("--capital", type=float, default=10_000.0)
    parser.add_argument("--risk-pct", type=float, default=1.0)
    parser.add_argument("--out-dir", default="optimize_results")
    parser.add_argument("--fixed", default=None,
                        help="고정할 파라미터 JSON (예: '{\"min_rr\":2.0}')")
    parser.add_argument("--tf-pairs", default=None,
                        help="탐색할 타임프레임 조합을 직접 지정 (예: '1h/15m,4h/1h'). "
                             "그리드의 tf_pair를 덮어씁니다.")
    return parser


def main():
    args = build_parser().parse_args()

    base = StrategyConfig(
        exchange=args.exchange, symbol=args.symbol, market_type=args.market_type,
        initial_capital=args.capital, risk_pct=args.risk_pct,
    )
    if args.fixed:
        base = apply_params(base, json.loads(args.fixed))

    # 기간을 학습 / 검증으로 나눈다
    full_start = pd.Timestamp(args.start, tz="UTC")
    full_end = pd.Timestamp(args.end, tz="UTC")
    split_at = full_start + (full_end - full_start) * args.split
    is_range = (full_start.strftime("%Y-%m-%d"), split_at.strftime("%Y-%m-%d"))
    oos_range = (split_at.strftime("%Y-%m-%d"), full_end.strftime("%Y-%m-%d"))

    print("━" * 70)
    print("  ICT FVG 파라미터 탐색")
    print("━" * 70)
    print(f"  종목    : {args.exchange} {args.symbol}")
    print(f"  전체    : {is_range[0]} ~ {oos_range[1]}")
    print(f"  학습(IS): {is_range[0]} ~ {is_range[1]}   ← 여기서만 최적화")
    print(f"  검증(OOS): {oos_range[0]} ~ {oos_range[1]}   ← 성과는 여기로 판단")
    print(f"  그리드  : {args.grid}   최소 거래수: {args.min_trades}")

    grid = dict(GRIDS[args.grid])
    if args.tf_pairs:
        pairs = [tuple(p.strip().split("/")) for p in args.tf_pairs.split(",")]
        grid["tf_pair"] = pairs
        print(f"  타임프레임: {', '.join(f'{h}/{l}' for h, l in pairs)} (직접 지정)")

    top, oos_df = run_search(args, base, grid, is_range, oos_range)
    if top is None:
        return 1

    print_comparison(top, oos_df, list(grid), is_range, oos_range)

    os.makedirs(args.out_dir, exist_ok=True)
    tag = f"{args.symbol.replace('/', '').replace(':', '')}_{args.grid}"
    combined = pd.concat(
        [top.add_prefix("IS_").reset_index(drop=True),
         oos_df.add_prefix("OOS_").reset_index(drop=True)], axis=1)
    path = os.path.join(args.out_dir, f"search_{tag}.csv")
    combined.to_csv(path, index=False)
    print(f"\n💾 결과 저장: {path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
