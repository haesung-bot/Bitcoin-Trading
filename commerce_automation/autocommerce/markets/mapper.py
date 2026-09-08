"""카테고리 매핑 + PricedProduct -> Listing 변환.

원본 사이트의 카테고리 문자열과 마켓의 카테고리 코드는 1:1 이 아니다.
매핑 테이블을 설정으로 빼 두고, 못 찾으면 기본 코드로 떨어뜨리되
경고를 남겨서 사람이 나중에 채울 수 있게 한다.
"""

from __future__ import annotations

from typing import Any

from ..models import Listing, MediaSet, PricedProduct
from ..utils.logging import get

log = get("market.mapper")


class CategoryMapper:
    def __init__(self, table: dict[str, dict[str, str]], defaults: dict[str, str]):
        # table: {"운동화": {"naver": "50002380", "coupang": "63903"}, ...}
        self.table = table or {}
        self.defaults = defaults or {}

    def resolve(self, category_path: list[str], market: str) -> tuple[str, str | None]:
        """(카테고리코드, 경고). 잎에서 뿌리 방향으로 훑는다."""
        for name in reversed(category_path or []):
            if (entry := self.table.get(name)) and entry.get(market):
                return entry[market], None
        fallback = self.defaults.get(market, "")
        warn = (
            f"카테고리 매핑 없음 {category_path} -> {market} 기본값 '{fallback}' 사용"
            if fallback else
            f"카테고리 매핑도 기본값도 없습니다 ({category_path} -> {market})"
        )
        return fallback, warn


def build_listing(priced: PricedProduct, media: MediaSet, market: str,
                  mapper: CategoryMapper, market_cfg: dict[str, Any],
                  images: list[str] | None = None) -> tuple[Listing, list[str]]:
    """등록 직전 중립 표현을 만든다. (Listing, 경고 목록)"""
    raw = priced.raw
    warnings: list[str] = []

    code, warn = mapper.resolve(raw.category_path, market)
    if warn:
        warnings.append(warn)

    if images is None:
        images = [a.remote_url for a in media.assets if a.remote_url]
    if not images:
        warnings.append("대표이미지가 없습니다 — 마켓 이미지 업로드 결과가 비었습니다")

    total_stock = sum(o.stock for o in raw.options) or 0
    if total_stock <= 0:
        total_stock = int(market_cfg.get("default_stock", 10))
        warnings.append(f"옵션 재고가 0이라 기본 재고 {total_stock}으로 설정")

    return Listing(
        product_uid=raw.uid,
        market=market,
        title=priced.seo.title,
        sell_price=priced.price.sell_price,
        stock=total_stock,
        category_code=code,
        brand=raw.brand,
        manufacturer=raw.attributes.get("제조사", raw.brand),
        origin=market_cfg.get("origin", raw.attributes.get("원산지", "상세페이지 참조")),
        images=images,
        options=raw.options,
        detail_html=raw.description_html or _fallback_detail(priced),
        tags=priced.seo.tags,
        attributes=raw.attributes,
        shipping_mode=priced.price.shipping_mode,
        shipping_charge=priced.price.shipping_charge,
        free_ship_over=priced.price.free_ship_over,
    ), warnings


def _fallback_detail(priced: PricedProduct) -> str:
    raw = priced.raw
    rows = "".join(
        f"<tr><th>{k}</th><td>{v}</td></tr>"
        for k, v in raw.attributes.items()
        if k != "원본사이트"
    )
    return (
        f"<div><h2>{priced.seo.title}</h2>"
        f"<p>브랜드: {raw.brand}</p>"
        f"<table>{rows}</table>"
        f"<p>상품 상세 정보는 이미지에서 확인해 주세요.</p></div>"
    )


def build_mapper(settings) -> CategoryMapper:
    cfg = settings.section("markets")
    table = settings.get("categories.map", {}) or {}
    defaults = {
        "naver": (cfg.get("naver") or {}).get("default_category_id", ""),
        "coupang": (cfg.get("coupang") or {}).get("default_category_code", ""),
    }
    return CategoryMapper(table, defaults)


def build_adapter(market: str, settings, *, dry_run: bool | None = None):
    from ..utils.http import HttpClient
    from .coupang import CoupangAdapter
    from .naver import NaverAdapter

    adapters = {"naver": NaverAdapter, "coupang": CoupangAdapter}
    if market not in adapters:
        raise KeyError(f"지원하지 않는 마켓: {market} (가능: {', '.join(adapters)})")
    cfg = settings.section(f"markets.{market}")
    dr = settings.dry_run if dry_run is None else dry_run
    return adapters[market](cfg, dry_run=dr, http=HttpClient(default_rate=2.0))
