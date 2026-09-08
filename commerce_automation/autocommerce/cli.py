"""명령줄 인터페이스.

    python -m autocommerce doctor                    설정/의존성 점검
    python -m autocommerce crawl --source fixture    수집만 (JSON 출력)
    python -m autocommerce price  --cost 89000       단건 마진 시뮬레이션
    python -m autocommerce run    --source fixture --markets naver,coupang
    python -m autocommerce status                    현재 파이프라인 상태
    python -m autocommerce orders sync|list|approve|reject

표준 라이브러리 argparse 만 쓴다 — 서버에 추가 의존성을 깔지 않고 돌리기 위해.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

from . import config as configmod
from .models import to_dict
from .pricing.margin import MarginCalculator, MarginPolicy, load_policies
from .utils import logging as logmod

DEFAULT_CONFIG = "config/settings.yaml"


def _settings(args: argparse.Namespace):
    path = args.config
    if not Path(path).exists():
        example = Path(path).with_name("settings.example.yaml")
        if example.exists():
            print(
                f"[안내] {path} 가 없어 예시 설정({example})으로 실행합니다.\n"
                f"       실제 운영 전에 `cp {example} {path}` 후 값을 채우세요.\n",
                file=sys.stderr,
            )
            path = str(example)
        else:
            raise SystemExit(f"설정 파일을 찾을 수 없습니다: {path}")
    settings = configmod.load(path)
    if getattr(args, "live", False):
        settings.raw.setdefault("runtime", {})["dry_run"] = False
    logmod.setup(
        settings.get("runtime.log_level", "INFO"),
        settings.workdir / "autocommerce.log",
    )
    return settings


# --------------------------------------------------------------- doctor
def cmd_doctor(args: argparse.Namespace) -> int:
    settings = _settings(args)
    print("=" * 64)
    print(f" 설정 파일 : {settings.path}")
    print(f" 모드      : {'DRY-RUN (전송 안 함)' if settings.dry_run else 'LIVE (실제 등록)'}")
    print(f" 작업 폴더 : {settings.workdir.resolve()}")
    print(f" DB        : {settings.db_path}")
    print("=" * 64)

    print("\n[의존성]")
    for mod, why in [("requests", "필수"), ("yaml", "필수"),
                     ("PIL", "이미지 규격화"), ("bcrypt", "네이버 인증")]:
        try:
            __import__(mod)
            print(f"  ✓ {mod:<10} ({why})")
        except ImportError:
            mark = "✗" if why == "필수" else "○"
            print(f"  {mark} {mod:<10} ({why}) — pip install "
                  f"{'Pillow' if mod == 'PIL' else mod}")

    print("\n[크롤 소스]")
    sources = settings.get("crawler.sources", {}) or {}
    for name, cfg in sources.items():
        state = "사용" if cfg.get("enabled") else "비활성"
        print(f"  · {name:<10} {state}")
    print(f"  robots.txt 준수: {settings.get('crawler.respect_robots')}")
    print(f"  요청 속도      : {settings.get('crawler.rate_per_sec')} req/s")

    print("\n[마진 정책]")
    for name, pol in load_policies(settings).items():
        marks = " (기본)" if name == settings.get("pricing.default_policy") else ""
        print(f"  · {name}{marks}: 목표 {pol.target_margin:.0%} / "
              f"기준 {pol.margin_basis} / VAT {pol.vat_mode}")
        for market in pol.fees:
            print(f"      {market} 실효수수료 {pol.fee_rate(market):.2%}")

    print("\n[마켓]")
    ok_all = True
    for market in ("naver", "coupang"):
        cfg = settings.section(f"markets.{market}")
        if not cfg:
            continue
        ready, missing = settings.credentials_ready(market)
        enabled = cfg.get("enabled")
        icon = "✓" if ready else "✗"
        if not ready:
            ok_all = False
        print(f"  {icon} {market:<8} enabled={enabled} "
              f"{'자격증명 OK' if ready else '누락: ' + ', '.join(missing)}")
    if not ok_all and not settings.dry_run:
        print("\n  ⚠ LIVE 모드인데 자격증명이 비어 있습니다.")
        return 1

    print("\n[이미지]")
    img = settings.section("imaging")
    print(f"  provider={img.get('provider')} size={img.get('size')} "
          f"enabled={img.get('enabled')}")
    lic = img.get("licensing", {}) or {}
    print(f"  저작권 가드: require_license={lic.get('require_source_license')} "
          f"허용소스={lic.get('licensed_sources')}")
    print()
    return 0


# --------------------------------------------------------------- crawl
def cmd_crawl(args: argparse.Namespace) -> int:
    from .crawlers.registry import build as build_crawler

    settings = _settings(args)
    crawler = build_crawler(args.source, settings)
    params = json.loads(args.params) if args.params else {}
    items = [to_dict(p) for p in crawler.crawl(limit=args.limit, **params)]
    print(json.dumps(items, ensure_ascii=False, indent=2))
    print(crawler.report.summary(), file=sys.stderr)
    return 0


# --------------------------------------------------------------- price
def cmd_price(args: argparse.Namespace) -> int:
    settings = _settings(args)
    policies = load_policies(settings)
    name = args.policy or settings.get("pricing.default_policy", "standard")
    policy = policies.get(name) or MarginPolicy(name=name)
    calc = MarginCalculator(policy)

    markets = args.markets.split(",")
    print(f"\n정책 '{policy.name}' | 목표 {policy.target_margin:.0%} "
          f"({policy.margin_basis} 기준) | VAT {policy.vat_mode}")
    print(f"원가 {args.cost:,}원 + 매입배송 {policy.inbound_shipping:,} "
          f"+ 발송배송 {policy.outbound_shipping:,} + 부자재 {policy.packaging:,}\n")
    print(f"{'마켓':<8}{'판매가':>12}{'수수료':>12}{'VAT':>10}"
          f"{'순이익':>12}{'마진율':>9}")
    print("-" * 66)
    for market in markets:
        bd = calc.compute(args.cost, market.strip())
        print(f"{market:<10}{bd.sell_price:>12,}{bd.commission_amount:>12,}"
              f"{bd.vat_payable:>10,}{bd.net_profit:>12,}{bd.margin_rate:>8.1%}")
        for w in bd.warnings:
            print(f"           ⚠ {w}")
    print()
    return 0


# --------------------------------------------------------------- run
def cmd_run(args: argparse.Namespace) -> int:
    from .pipeline import Pipeline

    settings = _settings(args)
    pipe = Pipeline(settings)
    mode = "LIVE — 실제 마켓에 등록됩니다" if not pipe.dry_run else "DRY-RUN"
    print(f"\n▶ 파이프라인 시작 [{mode}]\n")
    try:
        stats = pipe.run(
            args.source, [m.strip() for m in args.markets.split(",")],
            limit=args.limit, force=args.force, allow_warnings=args.allow_warnings,
            **(json.loads(args.params) if args.params else {}),
        )
    finally:
        pipe.close()

    print(f"\n■ 결과 (run {stats.run_id})")
    print(stats.table())
    if stats.warnings:
        print(f"\n경고 {len(stats.warnings)}건 (상위 10건):")
        for w in stats.warnings[:10]:
            print(f"  · {w}")
    if pipe.dry_run:
        print("\n※ DRY-RUN 이라 실제 등록은 하지 않았습니다. "
              "`autocommerce listings` 로 생성된 페이로드를 확인하세요.")
    return 0 if stats.failed == 0 else 1


# --------------------------------------------------------------- status
def cmd_status(args: argparse.Namespace) -> int:
    from .storage import Store

    settings = _settings(args)
    store = Store(settings.db_path)
    counts = store.counts_by_stage()
    print("\n[단계별 상품 수]")
    for stage in ("crawled", "priced", "imaged", "listed", "failed"):
        print(f"  {stage:<10} {counts.get(stage, 0)}")
    print("\n[최근 등록 시도]")
    for row in store.recent_listings(10):
        flag = "DRY" if row["dry_run"] else ("OK" if row["ok"] else "ERR")
        print(f"  [{flag:<3}] {row['created_at']}  {row['market']:<8} {row['uid']}"
              f"{'  ' + (row['error'] or '')[:60] if row['error'] else ''}")
    pending = store.pending_tasks()
    print(f"\n[확인 대기 주문] {len(pending)}건")
    store.close()
    return 0


# --------------------------------------------------------------- listings
def cmd_listings(args: argparse.Namespace) -> int:
    from .storage import Store

    settings = _settings(args)
    store = Store(settings.db_path)
    rows = store.recent_listings(args.limit)
    out = [
        {
            "uid": r["uid"], "market": r["market"], "ok": bool(r["ok"]),
            "dry_run": bool(r["dry_run"]), "error": r["error"],
            "payload": json.loads(r["request_json"] or "{}"),
        }
        for r in rows
    ]
    print(json.dumps(out, ensure_ascii=False, indent=2))
    store.close()
    return 0


# --------------------------------------------------------------- orders
def cmd_orders(args: argparse.Namespace) -> int:
    from .orders.one_click import OneClickConfig, OneClickPurchase, row_to_task
    from .pipeline import Pipeline
    from .storage import Store

    settings = _settings(args)

    if args.action == "sync":
        pipe = Pipeline(settings)
        since = args.since or (datetime.now() - timedelta(days=1)).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
        try:
            created = pipe.sync_orders(
                [m.strip() for m in args.markets.split(",")], since
            )
        finally:
            pipe.close()
        print(f"신규 확인 구매 작업 {len(created)}건")
        return 0

    store = Store(settings.db_path)
    oc = OneClickPurchase(
        store, OneClickConfig.from_config(settings.get("orders.one_click", {}))
    )
    try:
        if args.action == "list":
            rows = store.pending_tasks()
            if not rows:
                print("확인 대기 중인 주문이 없습니다.")
            for row in rows:
                print(oc.card(row_to_task(row)))
            return 0
        if args.action == "approve":
            if not args.task_id:
                print("--task-id 가 필요합니다", file=sys.stderr)
                return 2
            oc.approve(args.task_id, order_no=args.order_no or "")
            print(f"승인 처리: {args.task_id}")
            return 0
        if args.action == "reject":
            if not args.task_id:
                print("--task-id 가 필요합니다", file=sys.stderr)
                return 2
            oc.reject(args.task_id, args.reason or "사유 미기재")
            print(f"거절 처리: {args.task_id}")
            return 0
    finally:
        store.close()
    return 2


# --------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="autocommerce",
        description="상품 수집 → 마진/SEO → 이미지 변환 → 마켓 등록 자동화 파이프라인",
    )
    p.add_argument("--config", default=DEFAULT_CONFIG, help="설정 파일 경로")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="설정/의존성/자격증명 점검").set_defaults(
        func=cmd_doctor
    )

    c = sub.add_parser("crawl", help="상품 수집만 실행 (JSON 출력)")
    c.add_argument("--source", default="fixture")
    c.add_argument("--limit", type=int, default=None)
    c.add_argument("--params", help='크롤러 파라미터 JSON, 예: \'{"category":"1234"}\'')
    c.set_defaults(func=cmd_crawl)

    pr = sub.add_parser("price", help="단건 마진 시뮬레이션")
    pr.add_argument("--cost", type=int, required=True, help="매입원가(VAT 포함)")
    pr.add_argument("--markets", default="naver,coupang")
    pr.add_argument("--policy", default=None)
    pr.set_defaults(func=cmd_price)

    r = sub.add_parser("run", help="전체 파이프라인 실행")
    r.add_argument("--source", default="fixture")
    r.add_argument("--markets", default="naver")
    r.add_argument("--limit", type=int, default=None)
    r.add_argument("--params", default=None)
    r.add_argument("--force", action="store_true", help="이미 등록된 상품도 재처리")
    r.add_argument("--allow-warnings", action="store_true",
                   help="보류 사유가 있어도 등록 진행 (권장하지 않음)")
    r.add_argument("--live", action="store_true",
                   help="실제 마켓에 등록 (기본은 dry-run)")
    r.set_defaults(func=cmd_run)

    sub.add_parser("status", help="파이프라인 현황").set_defaults(func=cmd_status)

    l = sub.add_parser("listings", help="생성된 등록 페이로드 확인")
    l.add_argument("--limit", type=int, default=5)
    l.set_defaults(func=cmd_listings)

    o = sub.add_parser("orders", help="주문 동기화 / 1-Click 확인 구매")
    o.add_argument("action", choices=["sync", "list", "approve", "reject"])
    o.add_argument("--markets", default="naver")
    o.add_argument("--since", default=None)
    o.add_argument("--task-id", dest="task_id", default=None)
    o.add_argument("--order-no", dest="order_no", default=None,
                   help="공홈에서 결제 완료 후 받은 주문번호")
    o.add_argument("--reason", default=None)
    o.add_argument("--live", action="store_true")
    o.set_defaults(func=cmd_orders)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
