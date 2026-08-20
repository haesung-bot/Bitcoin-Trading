# -*- coding: utf-8 -*-
"""
Funding Rate Arbitrage (Delta-Neutral) Backtester
=================================================

두 거래소(또는 현물/선물) 간 펀딩비 격차를 이용한 델타 중립 차익거래 백테스터.
펀딩 수익만 단순 합산하지 않고 거래수수료 / 슬리피지 / 가격갭(베이시스) 변동까지
모두 차감하여 순수익(Net PnL)을 산출한다.

--------------------------------------------------------------------------
[포지션 정의]
  f_spread(t) = funding_a(t) - funding_b(t)

  f_spread >= +entry_threshold  ->  A 숏 / B 롱   (dir_a = -1)
  f_spread <= -entry_threshold  ->  A 롱 / B 숏   (dir_a = +1)

  숏 포지션은 펀딩비가 양수일 때 수령, 롱 포지션은 양수일 때 지급.
  => 회차당 수령 펀딩비율 = -dir_a * f_a - dir_b * f_b
                          = -dir_a * (f_a - f_b)        (dir_b = -dir_a)
     즉 A숏/B롱(dir_a=-1)이면 +f_spread, A롱/B숏(dir_a=+1)이면 -f_spread.
     진입 조건상 항상 +|f_spread| 로 수령 방향이 된다.

[수량]
  동일 수량(delta-neutral) Q 를 양쪽에 동시 체결.
  Q = notional / mid_entry,  mid_entry = (P_a_entry + P_b_entry) / 2
  => 레그당 평균 명목금액 = notional

[베이시스(가격 갭) 손익]
  S(t) = P_a(t) - P_b(t)
  구간 증분 PnL = Q * dir_a * ( S(t) - S(t-1) )
  누적       PnL = Q * dir_a * ( S(exit) - S(entry) )
  (A숏/B롱이면 갭이 축소될수록 이익, 확대되면 손실)

[비용]
  거래 수수료 : 진입 Q*P_a*fee_a + Q*P_b*fee_b , 청산도 동일 방식 (총 4회 체결)
  슬리피지    : 왕복 총 slippage_round_trip 을 진입/청산 절반씩,
                레그 평균 명목금액(Q*mid) 기준으로 차감
  기본값 기준 왕복 총비용 = 0.05%*4 + 0.04% = 0.24% (명목금액 대비)

[Net PnL]
  Net PnL = 총 수령 펀딩비 - 총 거래수수료 - 총 슬리피지 + 베이시스 손익

--------------------------------------------------------------------------
[룩어헤드 방지 규약]
  * funding_a(t)/funding_b(t) 는 시각 t 에 정산되는 펀딩비.
  * 시각 t 의 펀딩비를 관측 -> 시각 t 종가에 진입 결정.
  * 진입 회차(t0) 의 펀딩비는 수령하지 않는다. t0+1 회차부터 수령.
  * 청산 회차의 펀딩비는 정산 후 청산하므로 수령/지급에 포함한다.
  (거래소의 predicted funding 을 쓰는 경우 --collect-entry-interval 로 변경 가능)

[청산 조건]
  1) |f_spread| <= exit_threshold        (격차 축소)
  2) f_spread 부호 반전                   (펀딩 방향 반전)
  3) 보유 회차 >= max_hold                (목표 보유기간 만료)
  4) 데이터 종료                          (강제 청산)
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


# ----------------------------------------------------------------------
# 설정
# ----------------------------------------------------------------------
@dataclass
class Config:
    notional: float = 10_000.0          # 레그당 평균 명목금액 (USD)
    capital: Optional[float] = None     # 수익률 계산 기준 자본. None 이면 notional 사용
    fee_a: float = 0.0005               # A 거래소 편도 테이커 수수료 (0.05%)
    fee_b: float = 0.0005               # B 거래소 편도 테이커 수수료 (0.05%)
    slippage_round_trip: float = 0.0004  # 왕복 총 슬리피지 (0.04%)
    entry_threshold: float = 0.0015     # 진입 기준 |펀딩 격차| (8h당 0.15%)
    exit_threshold: float = 0.0005      # 청산 기준 |펀딩 격차| (8h당 0.05%)
    max_hold: int = 9                   # 최대 보유 회차 (9회 = 3일)
    intervals_per_day: float = 3.0      # 하루 펀딩 정산 횟수 (8h -> 3)
    collect_entry_interval: bool = False  # 진입 회차 펀딩 수령 여부

    @property
    def capital_base(self) -> float:
        return self.capital if self.capital else self.notional


# ----------------------------------------------------------------------
# 데이터 모델
# ----------------------------------------------------------------------
@dataclass
class Row:
    ts: str
    price_a: float
    price_b: float
    funding_a: float
    funding_b: float

    @property
    def f_spread(self) -> float:
        return self.funding_a - self.funding_b

    @property
    def basis(self) -> float:
        """S(t) = P_a - P_b"""
        return self.price_a - self.price_b

    @property
    def mid(self) -> float:
        return (self.price_a + self.price_b) / 2.0


@dataclass
class IntervalLog:
    idx: int
    ts: str
    price_a: float
    price_b: float
    f_a: float
    f_b: float
    f_spread: float
    state: str          # FLAT / ENTRY / HOLD / EXIT
    side: str           # "" / "A숏/B롱" / "A롱/B숏"
    funding_pnl: float = 0.0
    basis_pnl: float = 0.0
    cost: float = 0.0
    net: float = 0.0
    equity: float = 0.0
    drawdown: float = 0.0


@dataclass
class Trade:
    no: int
    entry_idx: int
    exit_idx: int
    entry_ts: str
    exit_ts: str
    side: str
    dir_a: int
    qty: float
    intervals: int
    entry_basis: float
    exit_basis: float
    funding_pnl: float = 0.0
    fee_cost: float = 0.0
    slip_cost: float = 0.0
    basis_pnl: float = 0.0
    exit_reason: str = ""
    funding_legs: List[float] = field(default_factory=list)

    @property
    def total_cost(self) -> float:
        return self.fee_cost + self.slip_cost

    @property
    def net_pnl(self) -> float:
        return self.funding_pnl - self.total_cost + self.basis_pnl


@dataclass
class Result:
    intervals: List[IntervalLog]
    trades: List[Trade]
    config: Config
    equity_curve: List[float]
    mdd_abs: float
    mdd_pct: float
    days: float


# ----------------------------------------------------------------------
# 데이터 로딩
# ----------------------------------------------------------------------
COLUMN_ALIASES = {
    "ts": ["ts", "timestamp", "time", "datetime", "date", "시각", "시간"],
    "price_a": ["price_a", "pa", "a_price", "px_a", "a거래소가격", "a_거래소가격"],
    "price_b": ["price_b", "pb", "b_price", "px_b", "b거래소가격", "b_거래소가격"],
    "funding_a": ["funding_a", "fa", "f_a", "a_funding", "a펀딩비", "a_펀딩비"],
    "funding_b": ["funding_b", "fb", "f_b", "b_funding", "b펀딩비", "b_펀딩비"],
}


def _resolve(header: List[str]) -> dict:
    lowered = {h.strip().lower().lstrip("﻿"): h for h in header}
    mapping = {}
    for key, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in lowered:
                mapping[key] = lowered[alias]
                break
        else:
            raise ValueError(
                f"필수 컬럼 '{key}' 를 찾을 수 없습니다. "
                f"허용 헤더: {COLUMN_ALIASES[key]} / 입력 헤더: {header}"
            )
    return mapping


def load_rows(path: str, funding_unit: str = "decimal") -> List[Row]:
    scale = 0.01 if funding_unit == "percent" else 1.0
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise ValueError("CSV 헤더가 없습니다.")
        m = _resolve(list(reader.fieldnames))
        rows = []
        for r in reader:
            if not r.get(m["ts"]):
                continue
            rows.append(
                Row(
                    ts=r[m["ts"]].strip(),
                    price_a=float(r[m["price_a"]]),
                    price_b=float(r[m["price_b"]]),
                    funding_a=float(r[m["funding_a"]]) * scale,
                    funding_b=float(r[m["funding_b"]]) * scale,
                )
            )
    if len(rows) < 2:
        raise ValueError("최소 2개 구간 이상의 데이터가 필요합니다.")
    return rows


# ----------------------------------------------------------------------
# 백테스트 엔진
# ----------------------------------------------------------------------
def _entry_cost(cfg: Config, qty: float, row: Row) -> tuple:
    fee = qty * row.price_a * cfg.fee_a + qty * row.price_b * cfg.fee_b
    slip = (cfg.slippage_round_trip / 2.0) * (qty * row.mid)
    return fee, slip


def run_backtest(rows: List[Row], cfg: Config) -> Result:
    logs: List[IntervalLog] = []
    trades: List[Trade] = []
    equity = 0.0
    peak = 0.0
    mdd_abs = 0.0
    equity_curve: List[float] = []

    open_trade: Optional[Trade] = None
    trade_no = 0

    for i, row in enumerate(rows):
        state = "FLAT"
        side = ""
        funding_pnl = 0.0
        basis_pnl = 0.0
        cost = 0.0

        # ---------- 1. 보유 중이면 펀딩 정산 + 베이시스 마킹 ----------
        if open_trade is not None:
            side = open_trade.side
            prev = rows[i - 1]
            basis_pnl = open_trade.qty * open_trade.dir_a * (row.basis - prev.basis)
            open_trade.basis_pnl += basis_pnl

            collect = (i > open_trade.entry_idx) or cfg.collect_entry_interval
            if collect:
                rate = -open_trade.dir_a * row.f_spread
                funding_pnl = cfg.notional * rate
                open_trade.funding_pnl += funding_pnl
                open_trade.funding_legs.append(rate)

            held = i - open_trade.entry_idx

            # ---------- 2. 청산 판단 ----------
            reason = ""
            if abs(row.f_spread) <= cfg.exit_threshold:
                reason = "격차축소"
            elif row.f_spread * (-open_trade.dir_a) < 0:
                reason = "방향반전"
            elif held >= cfg.max_hold:
                reason = "보유만료"
            elif i == len(rows) - 1:
                reason = "데이터종료"

            if reason:
                fee, slip = _entry_cost(cfg, open_trade.qty, row)
                open_trade.fee_cost += fee
                open_trade.slip_cost += slip
                cost = fee + slip
                open_trade.exit_idx = i
                open_trade.exit_ts = row.ts
                open_trade.exit_basis = row.basis
                open_trade.intervals = held
                open_trade.exit_reason = reason
                trades.append(open_trade)
                open_trade = None
                state = "EXIT"
            else:
                state = "HOLD"

        # ---------- 3. 신규 진입 판단 ----------
        if open_trade is None and state != "EXIT" and i < len(rows) - 1:
            if abs(row.f_spread) >= cfg.entry_threshold:
                dir_a = -1 if row.f_spread > 0 else 1
                side = "A숏/B롱" if dir_a == -1 else "A롱/B숏"
                qty = cfg.notional / row.mid
                fee, slip = _entry_cost(cfg, qty, row)
                cost = fee + slip
                trade_no += 1
                open_trade = Trade(
                    no=trade_no,
                    entry_idx=i,
                    exit_idx=i,
                    entry_ts=row.ts,
                    exit_ts="",
                    side=side,
                    dir_a=dir_a,
                    qty=qty,
                    intervals=0,
                    entry_basis=row.basis,
                    exit_basis=row.basis,
                    fee_cost=fee,
                    slip_cost=slip,
                )
                state = "ENTRY"

        # ---------- 4. 구간 손익 집계 ----------
        net = funding_pnl + basis_pnl - cost
        equity += net
        peak = max(peak, equity)
        dd = equity - peak
        mdd_abs = min(mdd_abs, dd)
        equity_curve.append(equity)

        logs.append(
            IntervalLog(
                idx=i, ts=row.ts, price_a=row.price_a, price_b=row.price_b,
                f_a=row.funding_a, f_b=row.funding_b, f_spread=row.f_spread,
                state=state, side=side, funding_pnl=funding_pnl,
                basis_pnl=basis_pnl, cost=cost, net=net,
                equity=equity, drawdown=dd,
            )
        )

    days = _elapsed_days(rows, cfg)
    return Result(
        intervals=logs,
        trades=trades,
        config=cfg,
        equity_curve=equity_curve,
        mdd_abs=abs(mdd_abs),
        mdd_pct=abs(mdd_abs) / cfg.capital_base,
        days=days,
    )


def _elapsed_days(rows: List[Row], cfg: Config) -> float:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            t0 = datetime.strptime(rows[0].ts, fmt)
            t1 = datetime.strptime(rows[-1].ts, fmt)
            return max((t1 - t0).total_seconds() / 86400.0, 1e-9)
        except ValueError:
            continue
    return max((len(rows) - 1) / cfg.intervals_per_day, 1e-9)


# ----------------------------------------------------------------------
# 리포트
# ----------------------------------------------------------------------
def _pct(x: float, base: float) -> str:
    return f"{x / base * 100:+.4f}%"


def print_report(res: Result) -> None:
    cfg = res.config
    N = cfg.notional
    CB = cfg.capital_base

    print("=" * 118)
    print("  펀딩비 차익거래(Delta-Neutral) 백테스트 리포트")
    print("=" * 118)
    print(f"  레그당 명목금액 : ${N:,.0f}  (양방향 총 익스포저 ${N*2:,.0f})")
    print(f"  수수료          : A {cfg.fee_a*100:.3f}% / B {cfg.fee_b*100:.3f}% (편도, 왕복 4회 체결)")
    print(f"  슬리피지        : 왕복 총 {cfg.slippage_round_trip*100:.3f}%")
    rt = (cfg.fee_a + cfg.fee_b) * 2 + cfg.slippage_round_trip
    print(f"  왕복 총 거래비용: {rt*100:.3f}%  (손익분기 펀딩 누적 {rt*100:.3f}%)")
    print(f"  진입/청산 기준  : |f_spread| >= {cfg.entry_threshold*100:.3f}% / <= {cfg.exit_threshold*100:.3f}%")
    print(f"  최대 보유       : {cfg.max_hold} 회차")
    print()

    # ---------------- 구간별 시뮬레이션 표 ----------------
    print("-" * 118)
    print("  [1] 구간별 시뮬레이션 (금액 단위: USD)")
    print("-" * 118)
    hdr = (f"{'#':>2} {'시각':<16} {'P_A':>9} {'P_B':>9} {'갭':>7} "
           f"{'f_A':>8} {'f_B':>8} {'f_gap':>8} {'상태':<6} "
           f"{'펀딩':>9} {'베이시스':>9} {'비용':>8} {'순증감':>9} {'누적':>10} {'DD':>9}")
    print(hdr)
    print("-" * 118)
    for r in res.intervals:
        print(f"{r.idx:>2} {r.ts:<16} {r.price_a:>9,.0f} {r.price_b:>9,.0f} "
              f"{r.price_a-r.price_b:>7,.0f} "
              f"{r.f_a*100:>7.4f}% {r.f_b*100:>7.4f}% {r.f_spread*100:>7.4f}% "
              f"{r.state:<6} "
              f"{r.funding_pnl:>9,.2f} {r.basis_pnl:>9,.2f} {-r.cost:>8,.2f} "
              f"{r.net:>9,.2f} {r.equity:>10,.2f} {r.drawdown:>9,.2f}")
    print("-" * 118)
    print()

    # ---------------- 트레이드별 표 ----------------
    print("-" * 118)
    print("  [2] 트레이드별 손익 분해")
    print("-" * 118)
    print(f"{'No':>2} {'포지션':<8} {'진입':<16} {'청산':<16} {'회차':>4} "
          f"{'펀딩수익':>10} {'수수료':>9} {'슬리피지':>9} {'갭손익':>9} "
          f"{'순손익':>10} {'수익률':>9}  청산사유")
    print("-" * 118)
    for t in res.trades:
        print(f"{t.no:>2} {t.side:<8} {t.entry_ts:<16} {t.exit_ts:<16} {t.intervals:>4} "
              f"{t.funding_pnl:>10,.2f} {-t.fee_cost:>9,.2f} {-t.slip_cost:>9,.2f} "
              f"{t.basis_pnl:>9,.2f} {t.net_pnl:>10,.2f} "
              f"{t.net_pnl/CB*100:>8.4f}%  {t.exit_reason}")
    print("-" * 118)
    print()

    # ---------------- 최종 리포트 ----------------
    tot_funding = sum(t.funding_pnl for t in res.trades)
    tot_fee = sum(t.fee_cost for t in res.trades)
    tot_slip = sum(t.slip_cost for t in res.trades)
    tot_basis = sum(t.basis_pnl for t in res.trades)
    net = tot_funding - tot_fee - tot_slip + tot_basis
    n = len(res.trades)
    wins = [t for t in res.trades if t.net_pnl > 0]
    losses = [t for t in res.trades if t.net_pnl <= 0]
    win_rate = len(wins) / n * 100 if n else 0.0
    avg_win = sum(t.net_pnl for t in wins) / len(wins) if wins else 0.0
    avg_loss = sum(t.net_pnl for t in losses) / len(losses) if losses else 0.0
    gross_win = sum(t.net_pnl for t in wins)
    gross_loss = abs(sum(t.net_pnl for t in losses))
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
    held = sum(t.intervals for t in res.trades)
    exposure = held / max(len(res.intervals) - 1, 1) * 100

    print("=" * 118)
    print("  [3] 최종 리포트")
    print("=" * 118)
    print(f"  총 수령 펀딩비        : {tot_funding:>12,.2f} USD   ({_pct(tot_funding, CB)})")
    print(f"  (-) 거래 수수료       : {-tot_fee:>12,.2f} USD   ({_pct(-tot_fee, CB)})")
    print(f"  (-) 슬리피지          : {-tot_slip:>12,.2f} USD   ({_pct(-tot_slip, CB)})")
    print(f"  (+/-) 가격갭 변동 손익: {tot_basis:>12,.2f} USD   ({_pct(tot_basis, CB)})")
    print("  " + "-" * 60)
    print(f"  = 최종 순손익 (Net PnL): {net:>11,.2f} USD   ({_pct(net, CB)})")
    print()
    print(f"  기간                  : {res.days:.2f} 일 ({len(res.intervals)} 구간)")
    if res.days > 0:
        ann = net / CB * (365.0 / res.days) * 100
        print(f"  단순 연환산 수익률(APR): {ann:>10.2f}%   * 표본이 작을수록 신뢰도 낮음")
    print(f"  MDD (최대낙폭)        : {-res.mdd_abs:>12,.2f} USD   (-{res.mdd_pct*100:.4f}%)")
    print(f"  총 트레이드 수        : {n:>12}")
    print(f"  승 / 패               : {len(wins):>7} / {len(losses)}")
    print(f"  승률                  : {win_rate:>11.2f}%")
    print(f"  평균 수익 / 평균 손실 : {avg_win:>10,.2f} / {avg_loss:,.2f} USD")
    pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
    print(f"  Profit Factor         : {pf_s:>12}")
    print(f"  포지션 보유 회차/노출 : {held:>12} 회차 ({exposure:.1f}%)")
    print("=" * 118)


def export_csv(res: Result, path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["idx", "ts", "price_a", "price_b", "basis", "funding_a",
                    "funding_b", "f_spread", "state", "side", "funding_pnl",
                    "basis_pnl", "cost", "net", "equity", "drawdown"])
        for r in res.intervals:
            w.writerow([r.idx, r.ts, r.price_a, r.price_b,
                        round(r.price_a - r.price_b, 4), r.f_a, r.f_b,
                        round(r.f_spread, 8), r.state, r.side,
                        round(r.funding_pnl, 4), round(r.basis_pnl, 4),
                        round(-r.cost, 4), round(r.net, 4),
                        round(r.equity, 4), round(r.drawdown, 4)])


# ----------------------------------------------------------------------
def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="펀딩비 차익거래 델타 중립 백테스터",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--data", default="funding_arb/sample_data.csv",
                   help="CSV 경로 (컬럼: ts, price_a, price_b, funding_a, funding_b)")
    p.add_argument("--funding-unit", choices=["decimal", "percent"], default="decimal",
                   help="펀딩비 표기 단위. decimal=0.0015, percent=0.15")
    p.add_argument("--notional", type=float, default=10_000.0)
    p.add_argument("--capital", type=float, default=None,
                   help="수익률 산정 기준 자본. 미지정 시 notional 사용")
    p.add_argument("--fee-a", type=float, default=0.0005)
    p.add_argument("--fee-b", type=float, default=0.0005)
    p.add_argument("--slippage", type=float, default=0.0004, help="왕복 총 슬리피지")
    p.add_argument("--entry-threshold", type=float, default=0.0015)
    p.add_argument("--exit-threshold", type=float, default=0.0005)
    p.add_argument("--max-hold", type=int, default=9)
    p.add_argument("--intervals-per-day", type=float, default=3.0)
    p.add_argument("--collect-entry-interval", action="store_true",
                   help="진입 회차의 펀딩비도 수령 처리")
    p.add_argument("--export", default=None, help="구간별 결과 CSV 저장 경로")
    a = p.parse_args(argv)

    cfg = Config(
        notional=a.notional, capital=a.capital, fee_a=a.fee_a, fee_b=a.fee_b,
        slippage_round_trip=a.slippage, entry_threshold=a.entry_threshold,
        exit_threshold=a.exit_threshold, max_hold=a.max_hold,
        intervals_per_day=a.intervals_per_day,
        collect_entry_interval=a.collect_entry_interval,
    )
    try:
        rows = load_rows(a.data, a.funding_unit)
    except (OSError, ValueError) as e:
        print(f"[에러] 데이터 로딩 실패: {e}", file=sys.stderr)
        return 1

    res = run_backtest(rows, cfg)
    print_report(res)
    if a.export:
        export_csv(res, a.export)
        print(f"\n구간별 결과 저장 완료 -> {a.export}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
