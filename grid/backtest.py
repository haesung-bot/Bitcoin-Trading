"""
grid/backtest.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
그리드 트레이딩 백테스트 엔진.

일정 간격으로 매수·매도 지정가를 깔아두고, 가격이 오르내릴 때마다
낮은 데서 사고 높은 데서 파는 것을 반복합니다. 방향 예측이 필요 없고
변동성 자체가 수익원입니다.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ 그리드 백테스트가 거의 항상 틀리는 이유
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1시간봉에서 고가-저가가 3% 벌어졌다고 할 때, 그 안에서 가격이 격자를
몇 번 왕복했는지 알 수 없습니다. 한 번 쭉 올랐을 수도, 열 번 진동했을
수도 있는데 OHLC 네 숫자로는 구분되지 않습니다.

실제로 BTC 3.6년 기준, 격자 간격 0.5%에서 체결 횟수 추정이
가정에 따라 8배까지 벌어집니다. 체결 횟수가 8배면 수익도 8배입니다.
즉 결과 숫자를 전략이 아니라 '체결 가정'이 결정합니다.

그래서 이 엔진은:
  · 가능한 한 잘게 쪼갠 데이터(5분봉 권장)로 경로를 시뮬레이션하고
  · 보수 / 중립 / 낙관 세 가정의 결과를 '전부' 함께 보여줍니다
하나만 보여주는 그리드 백테스트는 믿지 마십시오.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ 두 번째 함정: 그리드 수익만 세는 것
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
그리드는 옵션으로 치면 '스트래들 매도'와 같은 손익 구조입니다.
작은 수익을 꾸준히 쌓다가 큰 움직임 한 번에 반납합니다.

가격이 범위를 벗어나면 한쪽 포지션만 남습니다. 롱 그리드는 하단
이탈 시 고점에서 산 물량을 전부 안게 됩니다. 이때 '실현된 그리드
수익'은 여전히 플러스지만 평가손실이 그보다 훨씬 큽니다.

그래서 이 엔진은 실현손익과 미실현 평가손익을 항상 나눠서 보고합니다.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from dataclasses import dataclass, asdict, field

import numpy as np
import pandas as pd


# 체결 가정 — 한 봉 안에서 가격이 어떤 경로로 움직였다고 볼 것인가
FILL_ASSUMPTIONS = {
    # 봉 내부를 아예 무시하고 시가→종가 직선으로만 본다. 체결을 과소평가한다.
    "보수": "open_close",
    # 시가 → 가까운 극단 → 반대 극단 → 종가. 백테스트 표준 근사.
    "중립": "one_sweep",
    # 위 경로를 두 번 왕복했다고 본다. 체결을 과대평가한다.
    "낙관": "two_sweeps",
}


@dataclass
class GridConfig:
    # ─── 시장 ───
    exchange: str = "okx"
    symbol: str = "BTC/USDT:USDT"
    timeframe: str = "5m"          # 잘게 쪼갤수록 체결 추정이 정확해진다

    # ─── 격자 설계 ───
    lower: float = None            # 하단 가격 (None이면 시작가 기준 자동)
    upper: float = None            # 상단 가격
    auto_range_pct: float = 20.0   # 자동 설정 시 시작가 ±이 비율(%)
    grids: int = 50                # 격자 개수
    spacing: str = "geometric"     # 'arithmetic'(등차) 또는 'geometric'(등비)
    mode: str = "long"             # 'long'(롱 전용) 또는 'neutral'(중립, 롱+숏)

    # ─── 자금 ───
    investment: float = 10_000.0   # 총 투입 자본
    leverage: float = 1.0          # 1.0이면 현물과 동일
    maintenance_margin: float = 0.005  # 유지증거금률 (청산 판정용)

    # ─── 비용 (ICT/HA 전략과 동일하게 맞춤) ───
    maker_fee: float = 0.0002      # 그리드 주문은 지정가이므로 메이커
    taker_fee: float = 0.0005      # 강제 청산·범위이탈 정리 시

    # ─── 범위 이탈 처리 ───
    #  'hold' : 범위 밖에서는 거래를 멈추고 포지션을 그대로 보유 (일반적)
    #  'stop' : 범위를 벗어나면 전량 시장가 정리하고 종료 (손절형)
    on_exit: str = "hold"

    fill_assumption: str = "중립"

    def validate(self) -> "GridConfig":
        if self.mode not in ("long", "neutral"):
            raise ValueError(f"mode는 'long' 또는 'neutral'이어야 합니다: {self.mode!r}")
        if self.spacing not in ("arithmetic", "geometric"):
            raise ValueError(f"spacing은 'arithmetic' 또는 'geometric'이어야 합니다: {self.spacing!r}")
        if self.on_exit not in ("hold", "stop"):
            raise ValueError(f"on_exit는 'hold' 또는 'stop'이어야 합니다: {self.on_exit!r}")
        if self.fill_assumption not in FILL_ASSUMPTIONS:
            raise ValueError(
                f"fill_assumption은 {list(FILL_ASSUMPTIONS)} 중 하나여야 합니다: {self.fill_assumption!r}")
        if self.grids < 2:
            raise ValueError("격자는 2개 이상이어야 합니다.")
        if self.investment <= 0:
            raise ValueError("investment는 0보다 커야 합니다.")
        if self.leverage < 1:
            raise ValueError("leverage는 1 이상이어야 합니다.")
        if self.lower is not None and self.upper is not None and self.lower >= self.upper:
            raise ValueError(f"lower({self.lower})는 upper({self.upper})보다 작아야 합니다.")
        return self

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GridResult:
    realized_pnl: float            # 격자 왕복으로 확정된 수익
    unrealized_pnl: float          # 남은 포지션의 평가손익
    total_pnl: float
    fees: float
    fills: int                     # 총 체결 횟수
    round_trips: int               # 완성된 격자 왕복 수
    final_inventory: float
    avg_entry: float
    liquidated: bool
    exited: bool                   # on_exit='stop'으로 중도 종료됐는지
    out_of_range_pct: float        # 가격이 범위 밖에 있던 시간 비율
    equity_curve: pd.Series
    realized_curve: pd.Series
    max_drawdown: float
    buy_hold_pct: float
    config: dict
    levels: np.ndarray = field(repr=False, default=None)


def build_levels(cfg: GridConfig, reference_price: float) -> np.ndarray:
    """격자 가격들을 만든다.

    등차(arithmetic)는 간격이 일정하고, 등비(geometric)는 비율이 일정하다.
    가격대가 넓으면 등비가 맞다 — 등차로 하면 하단에서는 간격이
    상대적으로 너무 크고 상단에서는 너무 촘촘해진다.
    """
    lower = cfg.lower
    upper = cfg.upper
    if lower is None:
        lower = reference_price * (1 - cfg.auto_range_pct / 100.0)
    if upper is None:
        upper = reference_price * (1 + cfg.auto_range_pct / 100.0)
    if lower <= 0:
        raise ValueError(f"하단 가격이 0 이하입니다: {lower}")

    if cfg.spacing == "arithmetic":
        return np.linspace(lower, upper, cfg.grids + 1)
    return np.geomspace(lower, upper, cfg.grids + 1)


def _bar_path(prev_price, open_, high, low, close, assumption: str):
    """한 봉 안에서 가격이 지나간 경로를 근사한다.

    실제 경로는 알 수 없다. 그래서 세 가지 가정을 명시적으로 구분한다.
    """
    if assumption == "open_close":
        # 봉 내부를 무시 — 고가/저가를 아예 안 본다
        return [prev_price, open_, close]

    # 시가에서 더 가까운 극단을 먼저 찍었다고 본다 (표준 근사)
    if abs(open_ - low) <= abs(high - open_):
        sweep = [low, high]
    else:
        sweep = [high, low]

    if assumption == "one_sweep":
        return [prev_price, open_] + sweep + [close]
    # two_sweeps — 같은 왕복을 한 번 더 했다고 본다 (체결 과대평가)
    return [prev_price, open_] + sweep + sweep + [close]


def run_grid_backtest(df: pd.DataFrame, cfg: GridConfig) -> GridResult:
    """그리드 백테스트를 실행한다.

    ━━━ 격자 슬롯 모델 ━━━
    격자 i는 아래 두 가격을 짝으로 갖는다:
        매수 트리거 = levels[i]        (가격이 이 아래로 내려올 때 매수)
        매도 트리거 = levels[i+1]      (가격이 이 위로 올라갈 때 매도)
    한 왕복의 수익은 수량 × (levels[i+1] - levels[i]) - 수수료 이다.

    ⚠️ 격자 간격이 왕복 수수료보다 작으면 구조적으로 손실이다.
       이 함수는 그런 설정이면 실행 전에 경고한다.
    """
    cfg.validate()
    if df.empty:
        raise ValueError("데이터가 비어 있습니다.")

    open_ = df["open"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    n = len(df)

    levels = build_levels(cfg, open_[0])
    n_slots = len(levels) - 1
    assumption = FILL_ASSUMPTIONS[cfg.fill_assumption]

    # 격자당 수량: 모든 격자가 채워졌을 때 명목가치가 투입금×레버리지가 되도록
    mid = float(np.mean(levels))
    qty = cfg.investment * cfg.leverage / (n_slots * mid)

    slot_filled = np.zeros(n_slots, dtype=bool)
    slot_entry = np.zeros(n_slots, dtype=float)

    realized = 0.0
    fees_paid = 0.0
    fills = 0
    round_trips = 0
    inventory = 0.0        # 롱은 양수, 숏은 음수
    cost_basis = 0.0       # 보유 포지션의 취득원가 합
    liquidated = False
    exited = False
    out_of_range_bars = 0

    equity_curve = np.empty(n, dtype=float)
    realized_curve = np.empty(n, dtype=float)

    # 중립 모드에서는 시작가 위쪽 격자를 '숏 슬롯'으로 쓴다
    start_price = open_[0]
    short_slot = np.array([levels[i] >= start_price for i in range(n_slots)]) \
        if cfg.mode == "neutral" else np.zeros(n_slots, dtype=bool)

    prev_price = open_[0]

    for i in range(n):
        if not exited and not liquidated:
            path = _bar_path(prev_price, open_[i], high[i], low[i], close[i], assumption)

            for a, b in zip(path[:-1], path[1:]):
                if b == a:
                    continue
                rising = b > a
                lo, hi = (a, b) if rising else (b, a)

                # 이 구간에서 지나친 격자들을 찾는다
                first = int(np.searchsorted(levels, lo, side="right"))
                last = int(np.searchsorted(levels, hi, side="left"))
                if first >= last:
                    continue
                indices = range(first, last) if rising else range(last - 1, first - 1, -1)

                for level_idx in indices:
                    price = levels[level_idx]

                    if rising:
                        # 위로 통과 → 아래 슬롯(level_idx-1)의 매도 트리거
                        slot = level_idx - 1
                        if 0 <= slot < n_slots and slot_filled[slot] and not short_slot[slot]:
                            proceeds = qty * price
                            fee = proceeds * cfg.maker_fee
                            realized += proceeds - qty * slot_entry[slot] - fee
                            fees_paid += fee
                            inventory -= qty
                            cost_basis -= qty * slot_entry[slot]
                            slot_filled[slot] = False
                            fills += 1
                            round_trips += 1
                        # 중립 모드: 위로 통과하면 그 자리에서 신규 숏 진입
                        elif 0 <= level_idx < n_slots and short_slot[level_idx] \
                                and not slot_filled[level_idx]:
                            fee = qty * price * cfg.maker_fee
                            fees_paid += fee
                            realized -= fee
                            slot_filled[level_idx] = True
                            slot_entry[level_idx] = price
                            inventory -= qty
                            cost_basis -= qty * price
                            fills += 1
                    else:
                        # 아래로 통과
                        if 0 <= level_idx < n_slots and short_slot[level_idx]:
                            # 중립 모드 숏 슬롯의 커버 (숏은 위에서 팔고 아래에서 되산다)
                            slot = level_idx + 1
                            if slot < n_slots and slot_filled[slot] and short_slot[slot]:
                                cost = qty * price
                                fee = cost * cfg.maker_fee
                                realized += qty * slot_entry[slot] - cost - fee
                                fees_paid += fee
                                inventory += qty
                                cost_basis += qty * slot_entry[slot]
                                slot_filled[slot] = False
                                fills += 1
                                round_trips += 1
                        elif 0 <= level_idx < n_slots and not slot_filled[level_idx]:
                            # 롱 슬롯 매수
                            fee = qty * price * cfg.maker_fee
                            fees_paid += fee
                            realized -= fee
                            slot_filled[level_idx] = True
                            slot_entry[level_idx] = price
                            inventory += qty
                            cost_basis += qty * price
                            fills += 1

            prev_price = close[i]

        # ── 범위 이탈 및 청산 판정 ──
        price_now = close[i]
        if price_now < levels[0] or price_now > levels[-1]:
            out_of_range_bars += 1
            if cfg.on_exit == "stop" and not exited and not liquidated:
                # 전량 시장가 정리
                if inventory != 0:
                    proceeds = inventory * price_now
                    fee = abs(proceeds) * cfg.taker_fee
                    realized += proceeds - cost_basis - fee
                    fees_paid += fee
                    inventory = 0.0
                    cost_basis = 0.0
                exited = True

        unrealized = inventory * price_now - cost_basis
        equity = cfg.investment + realized + unrealized
        realized_curve[i] = cfg.investment + realized

        # 청산: 자기자본이 유지증거금 아래로 떨어지면 전액 손실 처리
        if not liquidated and cfg.leverage > 1:
            notional = abs(inventory) * price_now
            if equity <= notional * cfg.maintenance_margin:
                liquidated = True
                realized = -cfg.investment
                inventory = 0.0
                cost_basis = 0.0
                unrealized = 0.0
                equity = 0.0

        equity_curve[i] = max(equity, 0.0)

    price_now = close[-1]
    unrealized = inventory * price_now - cost_basis
    total = realized + unrealized

    curve = pd.Series(equity_curve, index=df.index, name="equity")
    running_peak = curve.cummax()
    drawdown = (curve / running_peak - 1.0).min() if len(curve) else 0.0

    # 그리드 간격이 수수료를 넘는지 점검 (구조적 손실 방지)
    gaps = np.diff(levels) / levels[:-1]
    min_gap_pct = float(gaps.min() * 100)
    breakeven_pct = 2 * cfg.maker_fee * 100
    if min_gap_pct <= breakeven_pct:
        print(f"⚠️ 격자 간격({min_gap_pct:.3f}%)이 왕복 수수료({breakeven_pct:.3f}%) 이하입니다. "
              f"구조적으로 손실이 나는 설정입니다. 격자 수를 줄이거나 범위를 넓히세요.")

    return GridResult(
        realized_pnl=realized,
        unrealized_pnl=unrealized,
        total_pnl=total,
        fees=fees_paid,
        fills=fills,
        round_trips=round_trips,
        final_inventory=inventory,
        avg_entry=(cost_basis / inventory) if inventory else 0.0,
        liquidated=liquidated,
        exited=exited,
        out_of_range_pct=out_of_range_bars / n * 100.0,
        equity_curve=curve,
        realized_curve=pd.Series(realized_curve, index=df.index, name="realized"),
        max_drawdown=float(drawdown) * 100.0,
        buy_hold_pct=(close[-1] / open_[0] - 1.0) * 100.0,
        config=cfg.to_dict(),
        levels=levels,
    )


def run_rolling_grid(df: pd.DataFrame, cfg: GridConfig, reset_days: int = 30) -> dict:
    """일정 주기마다 현재가 기준으로 격자 범위를 다시 잡는다.

    범위를 한 번 정해두고 방치하면, 가격이 범위를 벗어난 뒤로는 아무 일도
    일어나지 않아 백테스트가 무의미해진다. 실제로는 주기적으로 재설정하므로
    그 방식을 그대로 시뮬레이션한다.

    재설정 시점에 보유 포지션은 시장가로 전량 정리한다(테이커 수수료).
    이 정리 손실이 그리드 운용의 실제 비용이며, 보통 여기서 대부분 잃는다.
    """
    from dataclasses import replace

    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("시간 인덱스가 필요합니다.")

    period = pd.Timedelta(days=reset_days)
    start, end = df.index[0], df.index[-1]

    capital = cfg.investment
    segments = []
    cursor = start

    while cursor < end:
        stop = min(cursor + period, end)
        chunk = df.loc[cursor:stop]
        if len(chunk) < 10:
            break

        # 재설정 시점의 현재가를 중심으로 범위를 다시 잡고,
        # 구간이 끝나면 남은 포지션을 정리한다(on_exit와 무관하게).
        segment_cfg = replace(cfg, investment=capital, lower=None, upper=None)
        result = run_grid_backtest(chunk, segment_cfg)

        # 구간 종료 시 남은 포지션을 시장가 정리
        closing_fee = abs(result.final_inventory) * chunk["close"].iloc[-1] * cfg.taker_fee
        segment_pnl = result.total_pnl - closing_fee
        capital += segment_pnl

        segments.append({
            "시작": cursor, "종료": stop,
            "실현": result.realized_pnl,
            "평가": result.unrealized_pnl,
            "정리수수료": closing_fee,
            "구간손익": segment_pnl,
            "수익률%": segment_pnl / (capital - segment_pnl) * 100.0,
            "체결": result.fills,
            "범위밖%": result.out_of_range_pct,
            "자본": capital,
            "단순보유%": (chunk["close"].iloc[-1] / chunk["open"].iloc[0] - 1) * 100.0,
        })

        if capital <= 0:
            break
        cursor = stop

    table = pd.DataFrame(segments)
    return {
        "구간별": table,
        "최종자본": capital,
        "총수익률": (capital / cfg.investment - 1) * 100.0,
        "구간수": len(table),
        "수익구간": int((table["구간손익"] > 0).sum()) if len(table) else 0,
        "단순보유": (df["close"].iloc[-1] / df["open"].iloc[0] - 1) * 100.0,
    }


def compare_fill_assumptions(df: pd.DataFrame, cfg: GridConfig) -> pd.DataFrame:
    """같은 설정을 세 가지 체결 가정으로 돌려 비교한다.

    이 표의 폭이 곧 '이 백테스트를 얼마나 믿을 수 있는가'다.
    보수와 낙관의 차이가 크면 결과는 전략이 아니라 가정이 만든 것이다.
    """
    from dataclasses import replace

    rows = []
    for name in FILL_ASSUMPTIONS:
        result = run_grid_backtest(df, replace(cfg, fill_assumption=name))
        rows.append({
            "체결가정": name,
            "체결수": result.fills,
            "왕복수": result.round_trips,
            "실현손익": result.realized_pnl,
            "평가손익": result.unrealized_pnl,
            "총손익": result.total_pnl,
            "총수익률%": result.total_pnl / cfg.investment * 100.0,
            "수수료": result.fees,
            "MDD%": result.max_drawdown,
            "청산": result.liquidated,
        })
    return pd.DataFrame(rows)


def format_report(result: GridResult) -> str:
    """결과 요약. 실현손익과 평가손익을 반드시 분리해서 보여준다."""
    cfg = result.config
    investment = cfg["investment"]
    levels = result.levels

    lines = [
        "",
        "━" * 66,
        "  그리드 트레이딩 백테스트 결과",
        "━" * 66,
        f"  종목        : {cfg['exchange']} {cfg['symbol']} ({cfg['timeframe']})",
        f"  격자        : {cfg['grids']}개  {cfg['spacing']}  "
        f"[{levels[0]:,.0f} ~ {levels[-1]:,.0f}]",
        f"  모드        : {cfg['mode']}   레버리지 {cfg['leverage']}배   "
        f"범위이탈 시 {cfg['on_exit']}",
        f"  체결가정    : {cfg['fill_assumption']}",
        f"  기간        : {result.equity_curve.index[0]:%Y-%m-%d} ~ "
        f"{result.equity_curve.index[-1]:%Y-%m-%d}",
        "─" * 66,
        f"  {'그리드 실현손익':<18}: {result.realized_pnl:>15,.0f}  "
        f"({result.realized_pnl / investment * 100:+.1f}%)",
        f"  {'보유 평가손익':<18}: {result.unrealized_pnl:>15,.0f}  "
        f"({result.unrealized_pnl / investment * 100:+.1f}%)   ← 이걸 빼먹으면 안 됩니다",
        f"  {'총손익':<18}: {result.total_pnl:>15,.0f}  "
        f"({result.total_pnl / investment * 100:+.1f}%)",
        "─" * 66,
        f"  {'총 체결 횟수':<18}: {result.fills:>15,}",
        f"  {'완성된 왕복':<18}: {result.round_trips:>15,}",
        f"  {'수수료 합계':<18}: {result.fees:>15,.0f}",
        f"  {'최대 낙폭':<18}: {result.max_drawdown:>15.1f}%",
        f"  {'범위 밖 시간':<18}: {result.out_of_range_pct:>15.1f}%",
        f"  {'남은 포지션':<18}: {result.final_inventory:>15.4f}"
        + (f"  (평단 {result.avg_entry:,.0f})" if result.final_inventory else ""),
        "─" * 66,
        f"  {'단순보유 수익률':<18}: {result.buy_hold_pct:>15.1f}%",
        f"  {'그리드 총수익률':<18}: {result.total_pnl / investment * 100:>15.1f}%",
    ]

    if result.liquidated:
        lines.append("  🔴 청산되었습니다 — 투입금 전액 손실")
    if result.exited:
        lines.append("  ⏹️  범위 이탈로 중도 종료되었습니다")

    lines += [
        "━" * 66,
        "  ⚠️ 그리드는 스트래들 매도와 같은 손익 구조입니다. 작은 수익을 반복하다",
        "     큰 움직임 한 번에 반납합니다. '실현손익'만 보면 반드시 속습니다.",
        "━" * 66,
    ]
    return "\n".join(lines)
