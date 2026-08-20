"""
tests/test_grid.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
그리드 백테스트 엔진 검증.

가장 중요한 것:
  · 격자 한 왕복의 손익이 수식과 정확히 맞는가
  · 실현손익과 평가손익이 제대로 분리되는가
  · 체결 가정을 낙관적으로 바꾸면 체결이 늘어나는가 (단조성)
  · 범위 이탈 시 포지션이 남는 것이 반영되는가
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grid import (  # noqa: E402
    GridConfig, build_levels, run_grid_backtest, compare_fill_assumptions,
)
from test_ict_fvg import make_bars, synthetic_market  # noqa: E402


# ══════════════════════════════════════════════════════════════════
#  1. 격자 생성
# ══════════════════════════════════════════════════════════════════

def test_등차_격자_간격이_일정하다():
    cfg = GridConfig(lower=100, upper=200, grids=10, spacing="arithmetic")
    levels = build_levels(cfg, 150)
    assert len(levels) == 11
    gaps = np.diff(levels)
    assert np.allclose(gaps, 10.0), "등차 격자의 간격이 일정하지 않습니다"


def test_등비_격자_비율이_일정하다():
    cfg = GridConfig(lower=100, upper=200, grids=10, spacing="geometric")
    levels = build_levels(cfg, 150)
    ratios = levels[1:] / levels[:-1]
    assert np.allclose(ratios, ratios[0]), "등비 격자의 비율이 일정하지 않습니다"
    assert np.isclose(levels[0], 100) and np.isclose(levels[-1], 200)


def test_자동범위는_시작가_기준으로_잡힌다():
    cfg = GridConfig(lower=None, upper=None, auto_range_pct=20.0, grids=10)
    levels = build_levels(cfg, 1000)
    assert np.isclose(levels[0], 800) and np.isclose(levels[-1], 1200)


# ══════════════════════════════════════════════════════════════════
#  2. ⭐ 격자 한 왕복의 손익이 수식과 맞는가
# ══════════════════════════════════════════════════════════════════

def test_한_왕복_손익이_수식과_일치():
    """수익 = 수량 × (매도가 - 매수가) - 수수료.

    가격을 딱 한 격자만큼 내렸다가 올려서 왕복 한 번만 일어나게 한다.
    """
    # 격자: 100, 110, ..., 200 (등차 10개)
    cfg = GridConfig(lower=100, upper=200, grids=10, spacing="arithmetic",
                     investment=10_000, leverage=1.0, mode="long",
                     maker_fee=0.001, fill_assumption="중립")

    # 150에서 시작 → 135까지 내려감(140 격자 매수) → 155까지 올라감(150 격자 매도)
    df = make_bars([
        (150, 150, 150, 150),
        (150, 150, 135, 135),   # 140 격자를 아래로 통과 → 매수
        (135, 155, 135, 155),   # 140, 150 격자를 위로 통과 → 슬롯(140) 매도@150
    ])
    r = run_grid_backtest(df, cfg)

    levels = r.levels
    mid = float(np.mean(levels))
    qty = cfg.investment * cfg.leverage / (10 * mid)

    # 140에 사서 150에 판 한 왕복
    expected_gross = qty * (150 - 140)
    expected_fee = qty * 140 * cfg.maker_fee + qty * 150 * cfg.maker_fee

    assert r.round_trips == 1, f"왕복이 1회여야 하는데 {r.round_trips}회입니다"
    assert np.isclose(r.realized_pnl, expected_gross - expected_fee), (
        f"실현손익이 수식과 다릅니다 (실제 {r.realized_pnl:.6f}, "
        f"기대 {expected_gross - expected_fee:.6f})"
    )


def test_매수만_하고_안팔면_실현손익은_수수료뿐():
    """가격이 계속 내려가기만 하면 매수만 쌓인다.
    이때 실현손익은 수수료만큼 마이너스여야 하고, 손실은 전부 평가손익이어야 한다.
    """
    cfg = GridConfig(lower=100, upper=200, grids=10, spacing="arithmetic",
                     investment=10_000, leverage=1.0, mode="long",
                     maker_fee=0.001, fill_assumption="중립")
    df = make_bars([
        (200, 200, 200, 200),
        (200, 200, 105, 105),   # 190~110 격자를 전부 아래로 통과 → 매수만
    ])
    r = run_grid_backtest(df, cfg)

    assert r.round_trips == 0, "매도가 없었는데 왕복이 잡혔습니다"
    assert r.realized_pnl < 0, "실현손익이 수수료만큼 마이너스여야 합니다"
    assert np.isclose(r.realized_pnl, -r.fees), (
        f"실현손익({r.realized_pnl:.4f})이 수수료({-r.fees:.4f})와 다릅니다"
    )
    assert r.final_inventory > 0, "매수한 물량이 남아 있어야 합니다"
    assert r.unrealized_pnl < 0, "가격이 평단 아래이므로 평가손실이어야 합니다"


# ══════════════════════════════════════════════════════════════════
#  3. ⭐ 체결 가정의 단조성
# ══════════════════════════════════════════════════════════════════

def test_체결가정이_낙관적일수록_체결이_많아진다():
    """보수 ≤ 중립 ≤ 낙관 순으로 체결 횟수가 늘어나야 한다.
    이 순서가 깨지면 경로 시뮬레이션이 잘못된 것이다."""
    df = synthetic_market(n=2000, seed=13, freq="5min")
    cfg = GridConfig(auto_range_pct=25.0, grids=40, investment=10_000)

    table = compare_fill_assumptions(df, cfg).set_index("체결가정")
    보수, 중립, 낙관 = table.loc["보수", "체결수"], table.loc["중립", "체결수"], table.loc["낙관", "체결수"]

    assert 보수 <= 중립 <= 낙관, (
        f"체결 가정의 단조성이 깨졌습니다 (보수 {보수}, 중립 {중립}, 낙관 {낙관})"
    )
    assert 낙관 > 보수, "낙관 가정이 보수 가정보다 체결이 많아야 합니다"


def test_세_가정의_결과_폭이_보고된다():
    """세 가정의 총수익률 차이가 곧 '이 백테스트를 얼마나 믿을 수 있는가'다."""
    df = synthetic_market(n=2000, seed=13, freq="5min")
    table = compare_fill_assumptions(df, GridConfig(auto_range_pct=25.0, grids=40))
    assert set(table["체결가정"]) == {"보수", "중립", "낙관"}
    assert table["총손익"].notna().all()


# ══════════════════════════════════════════════════════════════════
#  4. 범위 이탈 처리
# ══════════════════════════════════════════════════════════════════

def test_범위_이탈시_hold는_포지션을_안고_간다():
    cfg = GridConfig(lower=100, upper=200, grids=10, spacing="arithmetic",
                     investment=10_000, mode="long", on_exit="hold")
    df = make_bars([
        (150, 150, 150, 150),
        (150, 150, 100, 100),   # 전 격자 매수
        (100, 100, 60, 60),     # 범위 하단 이탈
    ])
    r = run_grid_backtest(df, cfg)
    assert r.final_inventory > 0, "hold 모드는 포지션을 그대로 보유해야 합니다"
    assert r.unrealized_pnl < 0, "범위 아래로 이탈했으므로 평가손실이어야 합니다"
    assert r.out_of_range_pct > 0, "범위 밖 시간이 기록되어야 합니다"
    assert not r.exited


def test_범위_이탈시_stop은_전량_정리한다():
    cfg = GridConfig(lower=100, upper=200, grids=10, spacing="arithmetic",
                     investment=10_000, mode="long", on_exit="stop")
    df = make_bars([
        (150, 150, 150, 150),
        (150, 150, 100, 100),
        (100, 100, 60, 60),     # 이탈 → 전량 정리
    ])
    r = run_grid_backtest(df, cfg)
    assert r.exited, "stop 모드는 범위 이탈 시 종료되어야 합니다"
    assert np.isclose(r.final_inventory, 0.0), "전량 정리되어야 합니다"
    assert np.isclose(r.unrealized_pnl, 0.0), "정리 후에는 평가손익이 0이어야 합니다"
    assert r.realized_pnl < 0, "고점에서 사서 저점에 팔았으므로 손실이어야 합니다"


# ══════════════════════════════════════════════════════════════════
#  5. 자본 정합성 및 손익 분해
# ══════════════════════════════════════════════════════════════════

def test_총손익은_실현과_평가의_합():
    df = synthetic_market(n=3000, seed=21, freq="5min")
    r = run_grid_backtest(df, GridConfig(auto_range_pct=20.0, grids=30))
    assert np.isclose(r.total_pnl, r.realized_pnl + r.unrealized_pnl), \
        "총손익이 실현+평가와 맞지 않습니다"


def test_자본곡선_마지막값이_총손익과_일치():
    df = synthetic_market(n=3000, seed=21, freq="5min")
    cfg = GridConfig(auto_range_pct=20.0, grids=30, investment=10_000)
    r = run_grid_backtest(df, cfg)
    if r.liquidated:
        return
    assert np.isclose(r.equity_curve.iloc[-1], cfg.investment + r.total_pnl), \
        "자본곡선 마지막 값이 총손익과 맞지 않습니다"


def test_수수료는_체결이_있으면_양수():
    df = synthetic_market(n=3000, seed=21, freq="5min")
    r = run_grid_backtest(df, GridConfig(auto_range_pct=20.0, grids=30))
    if r.fills > 0:
        assert r.fees > 0, "체결이 있었는데 수수료가 0입니다"


def test_잘못된_설정은_거부된다():
    for kwargs in ({"mode": "이상한값"}, {"spacing": "xx"}, {"grids": 1},
                   {"investment": 0}, {"leverage": 0.5}, {"lower": 200, "upper": 100},
                   {"on_exit": "xx"}, {"fill_assumption": "xx"}):
        try:
            GridConfig(**kwargs).validate()
        except ValueError:
            continue
        raise AssertionError(f"잘못된 설정이 통과했습니다: {kwargs}")


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
