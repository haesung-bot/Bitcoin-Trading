"""파이프라인 오케스트레이터.

    [1] 크롤러 ──► RawProduct
                     │
    [2] 마진/SEO ────┼──► PricedProduct
                     │
    [3] 이미지 ──────┼──► MediaSet
                     │
    [4] 마켓 등록 ───┴──► ListingResult

설계 원칙
  · 상품 1건의 실패가 배치를 죽이지 않는다 (건별 예외 격리 + FAILED 기록)
  · 모든 단계는 재실행 가능하다 (멱등키 = uid, 변경 감지 = content_hash)
  · 기본은 dry_run. 실제 등록은 명시적으로 --live 를 켜야 한다
  · 등록 직전 게이트에서 경고가 하나라도 있으면 기본은 '보류'
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable

from .config import Settings
from .imaging.pipeline import ImagePipeline
from .markets.mapper import build_adapter, build_listing, build_mapper
from .models import (
    ListingResult, MediaSet, PricedProduct, RawProduct, Stage,
)
from .pricing.margin import (
    MarginCalculator, MarginPolicy, PricingError, load_policies,
)
from .pricing.seo import SeoConfig, SeoEngine
from .storage import Store
from .utils.logging import get

log = get("pipeline")


@dataclass
class RunStats:
    run_id: str
    crawled: int = 0
    skipped: int = 0
    priced: int = 0
    imaged: int = 0
    listed: int = 0
    held: int = 0          # 경고로 보류
    failed: int = 0
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id, "crawled": self.crawled, "skipped": self.skipped,
            "priced": self.priced, "imaged": self.imaged, "listed": self.listed,
            "held": self.held, "failed": self.failed,
            "warning_count": len(self.warnings),
        }

    def table(self) -> str:
        return (
            f"  수집 {self.crawled}  |  스킵 {self.skipped}  |  가격 {self.priced}  |  "
            f"이미지 {self.imaged}  |  등록 {self.listed}  |  보류 {self.held}  |  "
            f"실패 {self.failed}"
        )


class Pipeline:
    def __init__(self, settings: Settings, *, dry_run: bool | None = None) -> None:
        self.settings = settings
        self.dry_run = settings.dry_run if dry_run is None else dry_run
        self.store = Store(settings.db_path)

        policies = load_policies(settings)
        name = settings.get("pricing.default_policy", "standard")
        if name not in policies:
            log.warning(f"정책 '{name}' 이 없어 내장 기본값을 사용합니다")
            policies[name] = MarginPolicy(name=name)
        self.policy = policies[name]
        self.calc = MarginCalculator(self.policy)

        self.seo = SeoEngine(SeoConfig.from_config(settings.section("seo")))
        self.images = ImagePipeline(settings.section("imaging"), settings.workdir)
        self.mapper = build_mapper(settings)

    # ================================================== 2. 가격 + SEO
    def price_one(self, product: RawProduct, market: str) -> PricedProduct:
        breakdown = self.calc.compute(product.cost_price, market)
        seo = self.seo.build(product, market=market)
        return PricedProduct(raw=product, price=breakdown, seo=seo)

    # ================================================== 3. 이미지
    def image_one(self, product: RawProduct) -> MediaSet:
        return self.images.process(product)

    # ================================================== 4. 등록
    def list_one(self, priced: PricedProduct, media: MediaSet, market: str,
                 *, allow_warnings: bool = False) -> tuple[ListingResult, list[str]]:
        adapter = build_adapter(market, self.settings, dry_run=self.dry_run)
        try:
            images = adapter.publish_images(media)
        except Exception as exc:
            log.warning(f"[{market}] 이미지 업로드 실패 {priced.raw.uid}: {exc}")
            images = []
        listing, warns = build_listing(
            priced, media, market, self.mapper,
            self.settings.section(f"markets.{market}"), images=images,
        )
        warns = list(warns) + list(priced.price.warnings) + list(priced.seo.warnings) \
            + list(media.warnings)

        blocking = [w for w in warns if self._is_blocking(w)]
        if blocking and not allow_warnings:
            return ListingResult(
                product_uid=listing.product_uid, market=market, ok=False,
                error="보류: " + " / ".join(blocking),
                request_payload=adapter.build_payload(listing), dry_run=self.dry_run,
            ), warns

        return adapter.create_listing(listing), warns

    @staticmethod
    def _is_blocking(warning: str) -> bool:
        """등록을 막아야 하는 경고인지. 돈과 직결되는 것만 막는다."""
        blockers = ("순이익", "손실", "오파싱", "대표이미지", "최소가", "최대가",
                    "카테고리 매핑도 기본값도 없습니다")
        return any(k in warning for k in blockers)

    # ================================================== 전체 실행
    def run(self, source: str, markets: Iterable[str], *, limit: int | None = None,
            force: bool = False, allow_warnings: bool = False,
            **crawl_params: Any) -> RunStats:
        from .crawlers.registry import build as build_crawler

        run_id = uuid.uuid4().hex[:12]
        stats = RunStats(run_id=run_id)
        markets = list(markets)
        self.store.start_run(run_id, f"run source={source} markets={','.join(markets)}")
        log.info(
            f"[run {run_id}] source={source} markets={markets} "
            f"dry_run={self.dry_run} policy={self.policy.name}"
        )

        crawler = build_crawler(source, self.settings)
        for product in crawler.crawl(limit=limit, **crawl_params):
            stats.crawled += 1
            proceed, reason = self.store.should_process(product, force=force)
            if not proceed:
                stats.skipped += 1
                log.info(f"스킵 {product.uid}: {reason}")
                continue
            log.info(f"처리 {product.uid}: {reason}")
            self.store.save_raw(product)

            try:
                media = self.image_one(product)
                self.store.save_media(product.uid, media)
                stats.imaged += 1
            except Exception as exc:
                log.warning(f"이미지 단계 실패 {product.uid}: {exc}")
                media = MediaSet(product_uid=product.uid,
                                 warnings=[f"이미지 실패: {exc}"])

            for market in markets:
                try:
                    priced = self.price_one(product, market)
                except PricingError as exc:
                    stats.failed += 1
                    self.store.mark_failed(product.uid, str(exc))
                    log.warning(f"가격 계산 실패 {product.uid}/{market}: {exc}")
                    continue
                self.store.save_priced(priced)
                stats.priced += 1

                try:
                    result, warns = self.list_one(
                        priced, media, market, allow_warnings=allow_warnings
                    )
                except Exception as exc:
                    stats.failed += 1
                    self.store.mark_failed(product.uid, f"{type(exc).__name__}: {exc}")
                    log.warning(f"등록 실패 {product.uid}/{market}: {exc}")
                    continue

                stats.warnings.extend(f"{product.uid}/{market}: {w}" for w in warns)
                self.store.save_listing(result)
                if result.ok:
                    stats.listed += 1
                elif result.error and result.error.startswith("보류"):
                    stats.held += 1
                    log.warning(f"보류 {product.uid}/{market}: {result.error}")
                else:
                    stats.failed += 1
                    self.store.mark_failed(product.uid, result.error or "unknown")
                    log.warning(f"등록 실패 {product.uid}/{market}: {result.error}")

        log.info(crawler.report.summary())
        self.store.finish_run(run_id, stats.as_dict())
        return stats

    # ================================================== 주문 동기화
    def sync_orders(self, markets: Iterable[str], since: str) -> list:
        from .orders.one_click import OneClickConfig, OneClickPurchase

        oc = OneClickPurchase(
            self.store,
            OneClickConfig.from_config(self.settings.get("orders.one_click", {})),
        )
        created = []
        for market in markets:
            adapter = build_adapter(market, self.settings, dry_run=self.dry_run)
            try:
                tasks = adapter.fetch_orders(since)
            except Exception as exc:
                log.warning(f"[{market}] 주문 조회 실패: {exc}")
                continue
            for task in tasks:
                prepared = oc.prepare(task)
                if self.store.upsert_task(prepared):
                    created.append(prepared)
        return created

    def close(self) -> None:
        self.store.close()
