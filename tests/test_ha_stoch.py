"""
tests/test_ha_stoch.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
하이킨 애쉬 + EMA200 + 스토캐스틱 RSI 전략 검증.

가장 중요한 두 가지:
  · 체결가가 하이킨 애쉬 가격이 아니라 원본 가격인가
  · 신호가 마감된 봉으로만 만들어지는가 (미래참조 없음)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ha_stoch import heikin_ashi, stoch_rsi, HAStochConfig, build_signals, run_backtest  # noqa: E402
from test_ict_fvg import make_bars, synthetic_market  # noqa: E402


# ══════════════════════════════════════════════════════════════════
#  1. 하이킨 애쉬 계산
# ══════════════════════════════════════════════════════════════════

def test_하이킨애쉬_공식():
    df = make_bars([
        (100, 110, 95, 105),
        (105, 120, 100, 118),
    ])
    ha = heikin_ashi(df)

    # HA종가 = (시+고+저+종)/4
    assert ha["ha_close"].iat[0] == (100 + 110 + 95 + 105) / 4      # 102.5
    assert ha["ha_close"].iat[1] == (105 + 120 + 100 + 118) / 4     # 110.75

    # 첫 봉의 HA시가 = (시가+종가)/2
    assert ha["ha_open"].iat[0] == (100 + 105) / 2                   # 102.5
    # 이후 HA시가 = (직전 HA시가 + 직전 HA종가)/2
    assert ha["ha_open"].iat[1] == (102.5 + 102.5) / 2               # 102.5

    # HA고가 = max(고가, HA시가, HA종가), HA저가 = min(저가, HA시가, HA종가)
    assert ha["ha_high"].iat[1] == max(120, 102.5, 110.75)
    assert ha["ha_low"].iat[1] == min(100, 102.5, 110.75)


def test_아래꼬리_없음_판정():
    """양봉 HA에서 '아래 꼬리 없음'은 원본 저가가 HA시가 아래로
    내려가지 않았다는 뜻이다 (HA저가 == HA시가)."""
    df = synthetic_market(n=400, seed=3, freq="1h")
    ha = heikin_ashi(df)

    bullish = ha["ha_close"] > ha["ha_open"]
    # ⚠️ np.isclose는 쓰면 안 된다. 기본 상대오차 1e-5는 3만 달러 가격에서
    #    ±0.3달러라, 저가가 HA시가보다 조금 낮은 경우를 '꼬리 없음'으로
    #    잘못 통과시킨다. ha_low는 min()으로 정확히 계산되므로 등호가 맞다.
    no_wick_by_definition = ha["ha_low"] == ha["ha_open"]
    no_wick_by_rule = df["low"] >= ha["ha_open"]

    both = bullish
    assert (no_wick_by_definition[both] == no_wick_by_rule[both]).all(), \
        "아래 꼬리 없음 판정이 HA저가==HA시가 정의와 어긋납니다"


# ══════════════════════════════════════════════════════════════════
#  2. 스토캐스틱 RSI
# ══════════════════════════════════════════════════════════════════

def test_스토캐스틱RSI_범위와_워밍업():
    df = synthetic_market(n=600, seed=5, freq="1h")
    k, d = stoch_rsi(df["close"])

    valid = np.isfinite(k)
    assert valid.any(), "K가 전부 NaN입니다"
    assert (k[valid] >= -1e-9).all() and (k[valid] <= 100 + 1e-9).all(), \
        "K가 0~100 범위를 벗어났습니다"

    # 워밍업 구간은 NaN이어야 한다 (덜 익은 값으로 신호를 내면 안 됨)
    warmup = 14 + 14 + 3 + 3
    assert not np.isfinite(k[:warmup]).any(), "워밍업 구간에 값이 채워져 있습니다"


# ══════════════════════════════════════════════════════════════════
#  3. ⭐ 체결가가 하이킨 애쉬 가격이 아닌지
# ══════════════════════════════════════════════════════════════════

def test_진입가는_원본_시가여야_한다():
    """HA 종가/시가에 체결시키면 시장에 없던 가격으로 거래한 게 되어
    수익률이 부풀려진다. 진입가는 반드시 '다음 봉의 원본 시가'여야 한다."""
    df = synthetic_market(n=3000, seed=11, freq="1h")
    cfg = HAStochConfig(timeframe="1h", slippage_bps=0.0)  # 슬리피지 0으로 두고 정확히 비교
    result = run_backtest(df, cfg)

    if result.trades.empty:
        raise AssertionError("거래가 없어 검증할 수 없습니다.")

    ha = heikin_ashi(df)
    opens = df["open"]

    for _, trade in result.trades.iterrows():
        entry_time = trade["entry_time"]
        assert entry_time in opens.index
        assert np.isclose(trade["entry_price"], opens.loc[entry_time]), (
            f"{entry_time} 진입가가 원본 시가와 다릅니다 "
            f"(진입 {trade['entry_price']}, 시가 {opens.loc[entry_time]})"
        )
        # HA 값과 우연히 같을 수는 있으나, 시가와 다르면 무조건 잘못이다
        assert not np.isclose(trade["entry_price"], ha["ha_close"].loc[entry_time]) \
            or np.isclose(ha["ha_close"].loc[entry_time], opens.loc[entry_time]), \
            "진입가가 하이킨 애쉬 종가로 체결됐습니다"


def test_손절익절가는_원본가격_범위_안에서_체결된다():
    df = synthetic_market(n=3000, seed=11, freq="1h")
    result = run_backtest(df, HAStochConfig(timeframe="1h"))
    if result.trades.empty:
        return

    for _, trade in result.trades.iterrows():
        if trade["exit_reason"] == "종료시_청산":
            continue
        bar = df.loc[trade["exit_time"]]
        # 슬리피지를 감안해 약간의 여유를 준다
        assert bar["low"] * 0.99 <= trade["exit_price"] <= bar["high"] * 1.01, (
            f"{trade['exit_time']} 청산가 {trade['exit_price']:.2f}가 "
            f"그 봉의 고저({bar['low']:.2f}~{bar['high']:.2f}) 밖입니다"
        )


# ══════════════════════════════════════════════════════════════════
#  4. 전략 규칙 준수
# ══════════════════════════════════════════════════════════════════

def test_손익비가_설정값과_일치():
    df = synthetic_market(n=3000, seed=11, freq="1h")
    for rr in (1.5, 2.0, 3.0):
        result = run_backtest(df, HAStochConfig(timeframe="1h", reward_risk=rr))
        if result.trades.empty:
            continue
        t = result.trades
        risk = (t["entry_price"] - t["stop_price"]).abs()
        reward = (t["target_price"] - t["entry_price"]).abs()
        assert np.allclose(reward / risk, rr), f"손익비가 {rr}로 설정됐는데 다릅니다"


def test_추세필터_준수():
    """롱은 종가가 EMA200 위일 때만, 숏은 아래일 때만 진입해야 한다."""
    df = synthetic_market(n=4000, seed=7, freq="1h")
    cfg = HAStochConfig(timeframe="1h")
    result = run_backtest(df, cfg)
    if result.trades.empty:
        return

    sig = build_signals(df, cfg)
    positions = {ts: i for i, ts in enumerate(df.index)}

    for _, trade in result.trades.iterrows():
        # 신호는 진입 봉의 '직전' 봉에서 확정됐다
        signal_idx = positions[trade["entry_time"]] - 1
        if trade["direction"] == "LONG":
            assert sig["above_ema"][signal_idx], "EMA200 아래에서 롱 진입했습니다"
        else:
            assert sig["below_ema"][signal_idx], "EMA200 위에서 숏 진입했습니다"


def test_포지션은_한번에_하나():
    df = synthetic_market(n=4000, seed=7, freq="1h")
    result = run_backtest(df, HAStochConfig(timeframe="1h"))
    if len(result.trades) < 2:
        return
    ordered = result.trades.sort_values("entry_time")
    assert (ordered["entry_time"].to_numpy()[1:]
            >= ordered["exit_time"].to_numpy()[:-1]).all(), "포지션이 겹쳤습니다"


def test_최종자본이_거래손익합과_일치():
    df = synthetic_market(n=4000, seed=7, freq="1h")
    cfg = HAStochConfig(timeframe="1h")
    result = run_backtest(df, cfg)
    if result.trades.empty:
        return
    expected = cfg.initial_capital + result.trades["net_pnl"].sum()
    assert abs(result.metrics["최종자본"] - expected) < 1e-6


# ══════════════════════════════════════════════════════════════════
#  5. ⭐ 미래참조 회귀 테스트
# ══════════════════════════════════════════════════════════════════

def test_미래_데이터를_잘라내도_과거_거래가_동일해야_한다():
    """데이터를 뒤에서 잘라내고 다시 돌렸을 때 과거 거래가 달라지면
    어딘가에서 미래 정보를 쓰고 있다는 뜻이다.

    ⚠️ 하이킨 애쉬는 직전 HA시가에 누적 의존하므로 시작점이 다르면
       값이 미세하게 달라질 수 있다. 그래서 앞쪽을 자르지 않고
       '뒤쪽만' 잘라내어 비교한다 (실전에서 시간이 흐르는 것과 같은 상황).
    """
    df = synthetic_market(n=6000, seed=11, freq="1h")
    cfg = HAStochConfig(timeframe="1h")

    full = run_backtest(df, cfg)
    short = run_backtest(df.iloc[:4000], cfg)

    if short.trades.empty:
        raise AssertionError("잘라낸 구간에서 거래가 없어 비교할 수 없습니다.")

    compared = short.trades.iloc[:-1]  # 마지막은 강제청산됐을 수 있음
    if compared.empty:
        return

    columns = ["direction", "entry_time", "entry_price", "stop_price",
               "target_price", "exit_time", "exit_price", "exit_reason"]
    expected = full.trades.iloc[:len(compared)][columns].reset_index(drop=True)
    actual = compared[columns].reset_index(drop=True)

    problems = []
    for column in columns:
        diff = expected[column].to_numpy() != actual[column].to_numpy()
        if diff.any():
            first = int(np.flatnonzero(diff)[0])
            problems.append(
                f"  · {column}: {int(diff.sum())}건 불일치 (첫 거래 #{first}: "
                f"전체={expected[column].iloc[first]}, 절단={actual[column].iloc[first]})"
            )

    assert not problems, "⚠️ 미래참조 발견!\n" + "\n".join(problems)


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
