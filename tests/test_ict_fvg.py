"""
tests/test_ict_fvg.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ICT FVG 전략 검증 테스트.

pytest로 실행:      pytest tests/test_ict_fvg.py -v
그냥 파이썬으로도:  python tests/test_ict_fvg.py

가장 중요한 테스트는 맨 마지막의 '미래참조 회귀 테스트'입니다.
데이터를 뒤에서 잘라낸 뒤 다시 돌렸을 때 과거 거래가 그대로 재현되지 않으면,
전략이 어딘가에서 미래 정보를 훔쳐보고 있다는 뜻입니다.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ict_fvg import StrategyConfig, detect_fvgs, run_backtest, scan_setups  # noqa: E402
from ict_fvg.fvg import BULLISH, BEARISH  # noqa: E402
from ict_fvg.structure import (  # noqa: E402
    find_swings, swing_table, build_ltf_structure, align_htf_range,
)


# ══════════════════════════════════════════════════════════════════
#  테스트용 데이터 생성
# ══════════════════════════════════════════════════════════════════

def make_bars(rows, start="2024-01-01", freq="15min"):
    """[(open, high, low, close), ...] 를 OHLCV DataFrame으로."""
    index = pd.date_range(start, periods=len(rows), freq=freq, tz="UTC")
    return pd.DataFrame(
        [{"open": o, "high": h, "low": l, "close": c, "volume": 100.0}
         for o, h, l, c in rows],
        index=index,
    )


def synthetic_market(n=4000, seed=7, start="2024-01-01", freq="15min"):
    """추세와 되돌림이 섞인 가짜 시세를 만든다.

    순수 랜덤워크는 스윙 구조가 잘 안 생겨서 전략이 거의 거래하지 않는다.
    그래서 완만한 사인파 추세 위에 랜덤 노이즈를 얹어 파동을 만든다.
    """
    rng = np.random.default_rng(seed)
    steps = np.arange(n)

    trend = 30_000 + 4_000 * np.sin(steps / 260.0) + 1_500 * np.sin(steps / 71.0)
    noise = np.cumsum(rng.normal(0, 22, n))
    close = trend + noise

    open_ = np.empty(n)
    open_[0] = close[0]
    open_[1:] = close[:-1]

    wick = np.abs(rng.normal(0, 26, n)) + 4
    high = np.maximum(open_, close) + wick
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 26, n)) - 4

    index = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close,
         "volume": rng.uniform(50, 500, n)},
        index=index,
    )


def resample_htf(ltf, rule="1h"):
    """LTF를 묶어서 HTF를 만든다. 실제 거래소 데이터와 같은 관계가 되도록."""
    htf = ltf.resample(rule, label="left", closed="left").agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }).dropna()
    return htf


# ══════════════════════════════════════════════════════════════════
#  1. FVG 탐지 — 사양서 그대로인지
# ══════════════════════════════════════════════════════════════════

def test_bullish_fvg_정의():
    """상승 FVG: Candle[i-2].high < Candle[i].low
       상단 = Candle[i].low, 하단 = Candle[i-2].high, CE = 중간값"""
    df = make_bars([
        (100, 105, 99, 104),    # i-2 : high = 105
        (104, 118, 103, 117),   # i-1 : 크게 상승
        (117, 122, 110, 120),   # i   : low = 110 > 105  → 갭 발생
    ])
    fvgs = detect_fvgs(df)

    assert len(fvgs) == 1, "상승 FVG 1개가 잡혀야 합니다"
    gap = fvgs[0]
    assert gap.direction == BULLISH
    assert gap.top == 110.0      # Candle[i].low
    assert gap.bottom == 105.0   # Candle[i-2].high
    assert gap.ce == 107.5       # (110 + 105) / 2
    assert gap.size == 5.0
    assert gap.created_idx == 2  # 세 번째 봉에서 확정


def test_bearish_fvg_정의():
    """하락 FVG: Candle[i-2].low > Candle[i].high
       상단 = Candle[i-2].low, 하단 = Candle[i].high, CE = 중간값"""
    df = make_bars([
        (120, 125, 118, 119),   # i-2 : low = 118
        (119, 120, 104, 105),   # i-1 : 크게 하락
        (105, 110, 100, 102),   # i   : high = 110 < 118 → 갭 발생
    ])
    fvgs = detect_fvgs(df)

    assert len(fvgs) == 1
    gap = fvgs[0]
    assert gap.direction == BEARISH
    assert gap.top == 118.0      # Candle[i-2].low
    assert gap.bottom == 110.0   # Candle[i].high
    assert gap.ce == 114.0
    assert gap.created_idx == 2


def test_갭이_없으면_탐지되지_않는다():
    """겹치는 구간이 있으면 FVG가 아니다."""
    df = make_bars([
        (100, 110, 95, 105),
        (105, 115, 100, 112),
        (112, 118, 108, 116),   # low 108 < 이전 high 110 → 겹침
    ])
    assert detect_fvgs(df) == []


def test_경계값은_갭이_아니다():
    """high[i-2] == low[i] 인 접점은 갭이 아니다 (부등호가 <, > 이므로)."""
    df = make_bars([
        (100, 105, 99, 104),
        (104, 118, 103, 117),
        (117, 122, 105, 120),   # low == 105 == 이전 high
    ])
    assert detect_fvgs(df) == []


# ══════════════════════════════════════════════════════════════════
#  2. 스윙 탐지와 확정 지연
# ══════════════════════════════════════════════════════════════════

def test_스윙_고점_탐지():
    df = make_bars([
        (10, 11, 9, 10),
        (10, 12, 9, 11),
        (11, 20, 10, 19),   # idx 2 — 주변보다 확실히 높음
        (19, 15, 12, 13),
        (13, 14, 11, 12),
    ])
    is_high, _ = find_swings(df, left=2, right=2)
    assert is_high[2], "가운데 봉이 스윙 고점으로 잡혀야 합니다"
    assert is_high.sum() == 1


def test_스윙_확정은_right봉_뒤에_이뤄진다():
    """available_at이 '스윙 발생 시각'이 아니라
       '확정 봉의 종가 시각'이어야 한다 — 미래참조 방지의 핵심."""
    df = make_bars([
        (10, 11, 9, 10), (10, 12, 9, 11), (11, 20, 10, 19),
        (19, 15, 12, 13), (13, 14, 11, 12), (12, 13, 10, 11),
    ], freq="1h")

    table = swing_table(df, left=2, right=2, tf_ms=3_600_000)
    highs = table[table["kind"] == "high"]
    assert len(highs) == 1

    row = highs.iloc[0]
    assert row["swing_time"] == df.index[2]
    # 확정 봉 = 2 + 2 = 4번, 그 봉의 종가 시각 = index[4] + 1시간
    expected = df.index[4] + pd.Timedelta(hours=1)
    assert row["available_at"] == expected, (
        f"확정 시각이 틀립니다. 기대={expected}, 실제={row['available_at']}"
    )


# ══════════════════════════════════════════════════════════════════
#  3. 유동성 사냥 vs 종가 이탈 구분
# ══════════════════════════════════════════════════════════════════

def test_꼬리로_저점을_찍고_회복하면_사냥으로_인식():
    cfg = StrategyConfig(ltf_swing_left=1, ltf_swing_right=1, setup_valid_bars=50)

    # idx 1에 스윙 저점(90) → idx 4에서 꼬리로 85까지 갔다가 종가는 95로 회복
    df = make_bars([
        (100, 102, 98, 100),
        (100, 101, 90, 99),     # 스윙 저점 = 90
        (99, 104, 97, 103),
        (103, 106, 100, 105),
        (105, 106, 85, 95),     # 꼬리로 90 아래를 찍고 종가는 위로 회복 → 사냥
        (95, 99, 94, 98),
    ])
    state = build_ltf_structure(df, cfg)
    assert state["bull_stage"][4] == 1, "매도측 유동성 사냥(1단계)으로 잡혀야 합니다"


def test_종가로_저점을_뚫으면_사냥이_아니다():
    cfg = StrategyConfig(ltf_swing_left=1, ltf_swing_right=1, setup_valid_bars=50)

    df = make_bars([
        (100, 102, 98, 100),
        (100, 101, 90, 99),     # 스윙 저점 = 90
        (99, 104, 97, 103),
        (103, 106, 100, 105),
        (105, 106, 85, 86),     # 종가도 90 아래 → 진짜 하락 이탈
        (86, 88, 84, 85),
    ])
    state = build_ltf_structure(df, cfg)
    assert state["bull_stage"][4] == 0, "종가 이탈은 상승 시나리오가 아닙니다"


# ══════════════════════════════════════════════════════════════════
#  4. HTF 정렬에 미래참조가 없는지
# ══════════════════════════════════════════════════════════════════

def test_HTF_레인지는_확정_전에는_보이지_않는다():
    """align_htf_range가 각 LTF 시점에서 쓰는 스윙 개수(sh_count)가
       실제 확정 시각(available_at) 기준과 정확히 일치하는지 확인."""
    ltf = synthetic_market(n=1200, seed=3)
    htf = resample_htf(ltf, "1h")
    cfg = StrategyConfig(htf="1h", ltf="15m")

    ctx = align_htf_range(htf, ltf.index, cfg)
    table = swing_table(htf, cfg.htf_swing_left, cfg.htf_swing_right, 3_600_000)
    high_times = pd.DatetimeIndex(table[table["kind"] == "high"]["available_at"])

    for pos in (300, 600, 900, 1199):
        now = ltf.index[pos]
        expected = int((high_times <= now).sum())
        actual = int(ctx["sh_count"][pos])
        assert actual == expected, (
            f"{now} 시점의 사용 가능 스윙 개수가 다릅니다 "
            f"(기대 {expected}, 실제 {actual}) — 미래참조 의심"
        )

        # 딜링 레인지는 이탈분만큼 확장되므로 스윙 값과 정확히 같지는 않다.
        # 대신 '그 시점까지 마감된 HTF 봉의 실제 고/저 범위'를 절대 벗어날 수 없다.
        # 이 범위를 넘었다면 아직 오지 않은 봉의 값을 쓴 것이다.
        closed = htf[htf.index + pd.Timedelta(hours=1) <= now]
        if len(closed) and np.isfinite(ctx["range_high"][pos]):
            assert ctx["range_high"][pos] <= closed["high"].max() + 1e-9, (
                f"{now}: 레인지 상단이 아직 마감되지 않은 봉의 고가를 참조했습니다"
            )
            assert ctx["range_low"][pos] >= closed["low"].min() - 1e-9, (
                f"{now}: 레인지 하단이 아직 마감되지 않은 봉의 저가를 참조했습니다"
            )


# ══════════════════════════════════════════════════════════════════
#  5. 백테스트가 정상 동작하고 규칙을 지키는지
# ══════════════════════════════════════════════════════════════════

def test_백테스트_실행_및_규칙_준수():
    ltf = synthetic_market(n=4000, seed=11)
    htf = resample_htf(ltf, "1h")
    cfg = StrategyConfig(htf="1h", ltf="15m", initial_capital=10_000,
                         risk_pct=1.0, min_rr=2.0)

    result = run_backtest(ltf, htf, cfg)

    assert len(result.equity_curve) == len(ltf)
    assert np.isfinite(result.equity_curve).all(), "자본 곡선에 NaN/inf가 있습니다"
    assert result.metrics["거래횟수"] == len(result.trades)

    if result.trades.empty:
        return

    trades = result.trades

    # ① 계획 손익비가 항상 min_rr 이상이어야 한다
    assert (trades["planned_rr"] >= cfg.min_rr - 1e-9).all(), \
        "min_rr 미달 주문이 체결됐습니다"

    # ② 롱은 손절 < 진입 < 목표, 숏은 반대
    longs = trades[trades["direction"] == "LONG"]
    shorts = trades[trades["direction"] == "SHORT"]
    assert (longs["stop_price"] < longs["entry_price"]).all()
    assert (longs["target_price"] > longs["entry_price"]).all()
    assert (shorts["stop_price"] > shorts["entry_price"]).all()
    assert (shorts["target_price"] < shorts["entry_price"]).all()

    # ③ 포지션은 한 번에 하나 — 진입 시각이 직전 청산 시각 이상이어야 한다
    ordered = trades.sort_values("entry_time")
    assert (ordered["entry_time"].to_numpy()[1:]
            >= ordered["exit_time"].to_numpy()[:-1]).all(), \
        "포지션이 겹쳤습니다 (동시에 여러 개 보유)"

    # ④ 수수료는 항상 양수
    assert (trades["fees"] > 0).all()

    # ⑤ 최종 자본이 거래 손익 합과 맞아야 한다
    expected_equity = cfg.initial_capital + trades["net_pnl"].sum()
    assert abs(result.metrics["최종자본"] - expected_equity) < 1e-6


def test_한_봉에서_손절과_익절이_겹치면_손절이_우선():
    """보수적 가정이 실제로 적용되는지 확인 (낙관적으로 잡으면 결과가 거짓이 됨)."""
    from ict_fvg.backtest import _check_exit

    cfg = StrategyConfig()
    position = {"direction": BULLISH, "entry_price": 100.0,
                "stop_price": 95.0, "target_price": 110.0}

    # 저가가 손절을, 고가가 익절을 동시에 건드린 봉
    exit_info = _check_exit(position, bar_high=112.0, bar_low=94.0, slip=0.0, cfg=cfg)
    assert exit_info["reason"] == "손절", "동시 도달 시 손절이 우선해야 합니다"


def test_필터를_끄면_거래가_늘어난다():
    """사냥/MSS 필터가 실제로 거래를 줄이고 있는지 (필터가 작동은 하는지) 확인."""
    ltf = synthetic_market(n=4000, seed=11)
    htf = resample_htf(ltf, "1h")

    strict = run_backtest(ltf, htf, StrategyConfig(htf="1h", ltf="15m"))
    loose = run_backtest(ltf, htf, StrategyConfig(htf="1h", ltf="15m",
                                                 require_sweep=False))

    assert loose.metrics["거래횟수"] >= strict.metrics["거래횟수"], \
        "필터를 껐는데 거래가 줄었다면 필터 로직이 뒤집혀 있습니다"


def test_현재_대기중인_진입자리_조회():
    ltf = synthetic_market(n=2000, seed=5)
    htf = resample_htf(ltf, "1h")
    cfg = StrategyConfig(htf="1h", ltf="15m")

    setups = scan_setups(ltf, htf, cfg)
    assert isinstance(setups, pd.DataFrame)
    if not setups.empty:
        assert (setups["rr"] >= cfg.min_rr).all()
        for _, row in setups.iterrows():
            if row["direction"] == "LONG":
                assert row["stop"] < row["entry"] < row["target"]
            else:
                assert row["stop"] > row["entry"] > row["target"]


# ══════════════════════════════════════════════════════════════════
#  6. ⭐ 미래참조 회귀 테스트 — 가장 중요
# ══════════════════════════════════════════════════════════════════

def test_미래_데이터를_잘라내도_과거_거래가_동일해야_한다():
    """전체 구간으로 돌린 결과와, 뒤를 잘라내고 돌린 결과를 비교한다.

    전략이 미래를 보지 않는다면, 앞부분 거래는 두 결과에서 완전히 같아야 한다.
    하나라도 다르면 어딘가에서 미래 정보가 새고 있다는 뜻이다.

    (잘라낸 쪽의 마지막 거래는 강제 청산될 수 있으므로 비교에서 제외한다)
    """
    full_ltf = synthetic_market(n=4000, seed=11)
    full_htf = resample_htf(full_ltf, "1h")
    cfg = StrategyConfig(htf="1h", ltf="15m")

    cut = 2500
    short_ltf = full_ltf.iloc[:cut]
    # HTF도 같은 시점에서 자른다. LTF 마지막 봉이 마감된 시각 이후의
    # HTF 봉은 그 시점에 존재할 수 없다.
    ltf_end = short_ltf.index[-1] + pd.Timedelta(minutes=15)
    short_htf = full_htf[full_htf.index + pd.Timedelta(hours=1) <= ltf_end]

    full_result = run_backtest(full_ltf, full_htf, cfg)
    short_result = run_backtest(short_ltf, short_htf, cfg)

    if short_result.trades.empty:
        raise AssertionError("잘라낸 구간에서 거래가 발생하지 않아 비교할 수 없습니다.")

    # 마지막 거래는 강제 청산됐을 수 있으므로 제외
    compared = short_result.trades.iloc[:-1]
    if compared.empty:
        return

    columns = ["direction", "entry_time", "entry_price", "stop_price",
               "target_price", "exit_time", "exit_price", "exit_reason"]
    expected = full_result.trades.iloc[:len(compared)][columns].reset_index(drop=True)
    actual = compared[columns].reset_index(drop=True)

    mismatches = []
    for column in columns:
        different = expected[column].to_numpy() != actual[column].to_numpy()
        if different.any():
            first = int(np.flatnonzero(different)[0])
            mismatches.append(
                f"  · {column}: {len(np.flatnonzero(different))}건 불일치 "
                f"(첫 거래 #{first}: 전체={expected[column].iloc[first]}, "
                f"절단={actual[column].iloc[first]})"
            )

    assert not mismatches, (
        "⚠️ 미래참조 발견! 데이터를 잘라내자 과거 거래가 달라졌습니다.\n"
        + "\n".join(mismatches)
    )


# ══════════════════════════════════════════════════════════════════

def _run_all():
    tests = [(name, obj) for name, obj in sorted(globals().items())
             if name.startswith("test_") and callable(obj)]

    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
        except AssertionError as exc:
            failed += 1
            print(f"❌ {name}\n   {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"💥 {name}\n   {type(exc).__name__}: {exc}")
        else:
            passed += 1
            print(f"✅ {name}")

    print(f"\n{'━' * 55}\n통과 {passed} / 실패 {failed} (전체 {len(tests)})\n{'━' * 55}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
