#!/usr/bin/env python3
"""
run_ict_backtest.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ICT High-Probability FVG 전략 백테스트 실행 스크립트.

사용 예:
  # 기본 (바이낸스 BTC/USDT 무기한, HTF 1시간 / LTF 15분)
  python run_ict_backtest.py --start 2024-01-01

  # 일봉 기준 구역 + 5분봉 진입
  python run_ict_backtest.py --htf 1d --ltf 5m --start 2024-01-01 --end 2024-12-31

  # 필터를 꺼서 비교 (사냥/MSS 없이 FVG만)
  python run_ict_backtest.py --start 2024-01-01 --no-sweep

  # 롱만, 손익비 3 이상만
  python run_ict_backtest.py --start 2024-01-01 --long-only --min-rr 3

  # 직접 준비한 CSV로 (네트워크 없이)
  python run_ict_backtest.py --ltf-csv ltf.csv --htf-csv htf.csv

  # 현재 대기 중인 진입 자리만 확인 (백테스트 없이)
  python run_ict_backtest.py --start 2024-06-01 --scan-only
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import argparse
import os
import sys

import pandas as pd

from ict_fvg import (
    StrategyConfig, fetch_ohlcv, load_csv, validate_ohlcv,
    run_backtest, scan_setups, describe_market_state,
)


def build_parser():
    parser = argparse.ArgumentParser(
        description="ICT High-Probability FVG 전략 백테스트",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    market = parser.add_argument_group("시장 / 데이터")
    market.add_argument("--exchange", default="binance", help="CCXT 거래소 ID")
    market.add_argument("--symbol", default="BTC/USDT", help="거래 심볼")
    market.add_argument("--market-type", default="future", choices=["future", "spot"])
    market.add_argument("--htf", default="1h", help="상위 타임프레임 (예: 1h, 4h, 1d)")
    market.add_argument("--ltf", default="15m", help="하위 타임프레임 (예: 5m, 15m)")
    market.add_argument("--start", default="2024-01-01", help="시작일 (YYYY-MM-DD)")
    market.add_argument("--end", default=None, help="종료일 (생략 시 현재까지)")
    market.add_argument("--ltf-csv", default=None, help="LTF CSV 경로 (지정 시 네트워크 미사용)")
    market.add_argument("--htf-csv", default=None, help="HTF CSV 경로")
    market.add_argument("--no-cache", action="store_true", help="캐시 무시하고 새로 받기")

    filters = parser.add_argument_group("필터")
    filters.add_argument("--no-sweep", action="store_true",
                         help="유동성 사냥 필터 해제 (레인지 내부 FVG도 허용)")
    filters.add_argument("--no-mss", action="store_true",
                         help="구조 전환(MSS) 확인 생략 — 사냥만으로 진입")
    filters.add_argument("--zone-mode", default="ce", choices=["ce", "full"],
                         help="구역 판정 기준 (ce=CE만, full=갭 전체)")
    filters.add_argument("--min-fvg-atr", type=float, default=0.15,
                         help="ATR 대비 최소 FVG 크기 (0이면 필터 해제)")
    filters.add_argument("--setup-valid-bars", type=int, default=40,
                         help="사냥/MSS 이후 셋업 유효 봉 수")

    trade = parser.add_argument_group("진입 / 청산")
    trade.add_argument("--min-rr", type=float, default=2.0, help="최소 손익비")
    trade.add_argument("--sl-mode", default="wider", choices=["fvg", "swing", "wider"])
    trade.add_argument("--tp-target", default="nearest", choices=["nearest", "range_high"])
    trade.add_argument("--long-only", action="store_true", help="롱만 거래")
    trade.add_argument("--short-only", action="store_true", help="숏만 거래")

    money = parser.add_argument_group("자금 관리 / 비용")
    money.add_argument("--capital", type=float, default=10_000.0, help="초기 자본 (USDT)")
    money.add_argument("--risk-pct", type=float, default=1.0, help="1회 거래당 리스크 (%%)")
    money.add_argument("--max-leverage", type=float, default=10.0)
    money.add_argument("--maker-fee", type=float, default=0.0002)
    money.add_argument("--taker-fee", type=float, default=0.0005)
    money.add_argument("--slippage-bps", type=float, default=1.0)

    output = parser.add_argument_group("출력")
    output.add_argument("--out-dir", default="backtest_results", help="결과 저장 폴더")
    output.add_argument("--no-save", action="store_true", help="파일로 저장하지 않음")
    output.add_argument("--show-trades", type=int, default=10,
                        help="화면에 보여줄 최근 거래 수 (0이면 생략)")
    output.add_argument("--scan-only", action="store_true",
                        help="백테스트 없이 현재 대기 중인 진입 자리만 출력")

    parser.add_argument("--config", default=None,
                        help="저장해둔 설정 JSON을 불러온다 (예: configs/best_btc_4h1h.json). "
                             "이후 지정한 다른 옵션이 이 값을 덮어쓴다.")
    return parser


def config_from_args(args, parser) -> StrategyConfig:
    if args.long_only and args.short_only:
        raise SystemExit("❌ --long-only와 --short-only는 같이 쓸 수 없습니다.")

    if args.config:
        # 파일 설정을 기본으로 깔고, 명령줄에서 '직접 지정한' 옵션만 덮어쓴다.
        # (기본값까지 덮어쓰면 파일 설정이 통째로 무시되므로 구분이 필요하다)
        from dataclasses import replace

        cfg = StrategyConfig.load(args.config)
        print(f"⚙️  설정 파일 적용: {args.config}")

        explicit = {}
        defaults = parser.parse_args([])
        overridable = {
            "exchange": "exchange", "symbol": "symbol", "market_type": "market_type",
            "htf": "htf", "ltf": "ltf", "min_rr": "min_rr", "sl_mode": "sl_mode",
            "tp_target": "tp_target", "zone_mode": "zone_mode",
            "min_fvg_atr": "min_fvg_atr", "setup_valid_bars": "setup_valid_bars",
            "capital": "initial_capital", "risk_pct": "risk_pct",
            "max_leverage": "max_leverage", "maker_fee": "maker_fee",
            "taker_fee": "taker_fee", "slippage_bps": "slippage_bps",
        }
        for arg_name, field_name in overridable.items():
            if getattr(args, arg_name) != getattr(defaults, arg_name):
                explicit[field_name] = getattr(args, arg_name)
        if args.no_sweep:
            explicit["require_sweep"] = False
        if args.no_mss:
            explicit["require_mss"] = False
        if args.long_only:
            explicit["allow_short"] = False
        if args.short_only:
            explicit["allow_long"] = False

        if explicit:
            print(f"   명령줄로 덮어쓴 항목: {', '.join(sorted(explicit))}")
        return replace(cfg, **explicit).validate()

    return StrategyConfig(
        exchange=args.exchange,
        symbol=args.symbol,
        market_type=args.market_type,
        htf=args.htf,
        ltf=args.ltf,
        require_sweep=not args.no_sweep,
        require_mss=not args.no_mss,
        zone_mode=args.zone_mode,
        min_fvg_atr=args.min_fvg_atr,
        setup_valid_bars=args.setup_valid_bars,
        min_rr=args.min_rr,
        sl_mode=args.sl_mode,
        tp_target=args.tp_target,
        allow_long=not args.short_only,
        allow_short=not args.long_only,
        initial_capital=args.capital,
        risk_pct=args.risk_pct,
        max_leverage=args.max_leverage,
        maker_fee=args.maker_fee,
        taker_fee=args.taker_fee,
        slippage_bps=args.slippage_bps,
    ).validate()


def load_data(args, cfg):
    """CSV가 지정됐으면 그걸 쓰고, 아니면 CCXT로 받아온다."""
    if args.ltf_csv or args.htf_csv:
        if not (args.ltf_csv and args.htf_csv):
            raise SystemExit("❌ --ltf-csv와 --htf-csv는 둘 다 지정해야 합니다.")
        print(f"📂 CSV 로드: {args.ltf_csv} / {args.htf_csv}")
        return load_csv(args.ltf_csv), load_csv(args.htf_csv)

    common = dict(
        exchange_id=cfg.exchange,
        symbol=cfg.symbol,
        start=args.start,
        end=args.end,
        market_type=cfg.market_type,
        use_cache=not args.no_cache,
    )
    ltf = fetch_ohlcv(timeframe=cfg.ltf, **common)
    htf = fetch_ohlcv(timeframe=cfg.htf, **common)
    return ltf, htf


def print_setups(ltf, htf, cfg):
    """현재 시장 상태와 대기 중인 진입 자리를 출력한다."""
    state = describe_market_state(ltf, htf, cfg)
    print("\n" + "━" * 62)
    print("  현재 시장 상태")
    print("━" * 62)
    for key, value in state.items():
        shown = f"{value:,.2f}" if isinstance(value, float) else str(value)
        print(f"  {key:<20}: {shown}")

    setups = scan_setups(ltf, htf, cfg)
    print("─" * 62)
    if setups.empty:
        print("  대기 중인 진입 자리 없음 (조건을 만족하는 FVG가 없습니다)")
    else:
        print(f"  대기 중인 진입 자리: {len(setups)}개")
        print()
        with pd.option_context("display.max_columns", None, "display.width", 200):
            print(setups.to_string(index=False,
                                   float_format=lambda v: f"{v:,.2f}"))
    print("━" * 62)


def main():
    parser = build_parser()
    args = parser.parse_args()
    cfg = config_from_args(args, parser)

    ltf, htf = load_data(args, cfg)
    validate_ohlcv(ltf, f"LTF({cfg.ltf})")
    validate_ohlcv(htf, f"HTF({cfg.htf})")

    if args.scan_only:
        print_setups(ltf, htf, cfg)
        return 0

    print(f"\n⚙️  백테스트 실행 중... (LTF {len(ltf):,}봉 / HTF {len(htf):,}봉)")
    result = run_backtest(ltf, htf, cfg)
    print(result.summary())

    if args.show_trades and not result.trades.empty:
        recent = result.trades.tail(args.show_trades)
        shown = recent[["direction", "entry_time", "entry_price", "exit_price",
                        "exit_reason", "net_pnl", "r_multiple", "planned_rr", "bars_held"]]
        print(f"\n  최근 거래 {len(recent)}건")
        print("─" * 62)
        with pd.option_context("display.max_columns", None, "display.width", 200):
            print(shown.to_string(index=False, float_format=lambda v: f"{v:,.2f}"))

    if not args.no_save:
        os.makedirs(args.out_dir, exist_ok=True)
        tag = f"{cfg.symbol.replace('/', '')}_{cfg.htf}_{cfg.ltf}"
        trades_path = os.path.join(args.out_dir, f"trades_{tag}.csv")
        equity_path = os.path.join(args.out_dir, f"equity_{tag}.csv")
        result.trades.to_csv(trades_path, index=False)
        result.equity_curve.to_csv(equity_path)
        print(f"\n💾 거래내역: {trades_path}")
        print(f"💾 자본곡선: {equity_path}")

    print_setups(ltf, htf, cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
