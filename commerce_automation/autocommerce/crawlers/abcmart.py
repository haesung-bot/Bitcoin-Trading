"""ABC마트 / 브랜드 공식몰 크롤러.

주의 — 이 어댑터는 '틀'이다. 실제 운영 전에 반드시:
  1. 대상 사이트 robots.txt 와 이용약관에서 수집 허용 범위를 확인할 것
  2. settings.yaml 의 `crawler.sources.abcmart.selectors` 를 실제 DOM 에 맞게 채울 것
     (사이트 개편 시 코드가 아니라 설정만 고치면 되도록 분리해 둠)
  3. rate_per_sec 을 낮게 유지할 것 (기본 0.5 = 2초에 1회)

파서는 표준 라이브러리 HTMLParser 기반이라 추가 의존성이 없다.
셀렉터 문법은 CSS 전체가 아니라 `tag.class` / `.class` / `#id` 만 지원한다.
정교한 셀렉터가 필요하면 selectolax 나 lxml 을 넣고 `_Extractor` 만 교체하면 된다.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

from ..models import ProductOption, RawProduct
from ..utils.logging import get
from .base import BaseCrawler

log = get("crawler.abcmart")

PRICE_RE = re.compile(r"[\d,]{3,}")
JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.S | re.I,
)


class _Extractor(HTMLParser):
    """`tag.class` / `.class` / `#id` 매칭으로 텍스트와 속성을 뽑는 최소 파서."""

    def __init__(self, selector: str, attr: str | None = None) -> None:
        super().__init__(convert_charrefs=True)
        self.attr = attr
        self.results: list[str] = []
        self._depth = 0
        self._buf: list[str] = []
        self.want_tag = self.want_cls = self.want_id = None
        if selector.startswith("#"):
            self.want_id = selector[1:]
        else:
            tag, _, cls = selector.partition(".")
            self.want_tag = tag or None
            self.want_cls = cls or None

    def _matches(self, tag: str, attrs: dict[str, str]) -> bool:
        if not (self.want_tag or self.want_cls or self.want_id):
            return False
        if self.want_tag and tag != self.want_tag:
            return False
        if self.want_id and attrs.get("id") != self.want_id:
            return False
        if self.want_cls and self.want_cls not in (attrs.get("class") or "").split():
            return False
        return True

    def handle_starttag(self, tag: str, attrs_list: list) -> None:
        attrs = {k: (v or "") for k, v in attrs_list}
        if self._depth:
            self._depth += 1
            return
        if self._matches(tag, attrs):
            if self.attr:
                if (val := attrs.get(self.attr)):
                    self.results.append(val)
                return
            self._depth = 1
            self._buf = []

    def handle_startendtag(self, tag: str, attrs_list: list) -> None:
        # <img ... /> 같은 self-closing 태그는 endtag 가 안 온다
        if self._depth:
            return
        attrs = {k: (v or "") for k, v in attrs_list}
        if self._matches(tag, attrs) and self.attr:
            if (val := attrs.get(self.attr)):
                self.results.append(val)

    def handle_endtag(self, tag: str) -> None:
        if self._depth:
            self._depth -= 1
            if self._depth == 0:
                self.results.append(" ".join(self._buf).strip())

    def handle_data(self, data: str) -> None:
        if self._depth:
            if (text := data.strip()):
                self._buf.append(text)


def select(html: str, selector: str, attr: str | None = None) -> list[str]:
    if not selector:
        return []
    ex = _Extractor(selector, attr)
    ex.feed(html)
    return ex.results


def select_one(html: str, selector: str, attr: str | None = None) -> str | None:
    got = select(html, selector, attr)
    return got[0] if got else None


def parse_price(text: str | None) -> int:
    if not text:
        return 0
    m = PRICE_RE.search(text)
    return int(m.group().replace(",", "")) if m else 0


class AbcMartCrawler(BaseCrawler):
    source = "abcmart"

    @property
    def base_url(self) -> str:
        return self.cfg.get("base_url", "https://www.abcmart.co.kr")

    @property
    def sel(self) -> dict[str, str]:
        return self.cfg.get("selectors", {})

    # ---------------------------------------------------------- 목록
    def discover(self, category: str | None = None, **params: Any) -> list[str]:
        category = category or self.cfg.get("default_category", "")
        list_path = self.cfg.get("list_path",
                                 "/display/category?categoryCode={category}")
        max_pages = int(self.cfg.get("max_pages", 1))

        urls: list[str] = []
        for page in range(1, max_pages + 1):
            path = list_path.format(category=category, page=page)
            list_url = urljoin(self.base_url, path)
            try:
                html = self.fetch(list_url)
            except Exception as exc:
                log.warning(f"목록 페이지 실패 {list_url}: {exc}")
                break
            links = select(html, self.sel.get("link", "a"), attr="href")
            page_urls = [urljoin(self.base_url, h) for h in links if h]
            if not page_urls:
                log.warning(
                    f"목록에서 링크를 못 찾음 ({list_url}). "
                    f"selectors.link 설정을 실제 DOM 에 맞게 고치세요."
                )
                break
            urls.extend(page_urls)
        return list(dict.fromkeys(urls))  # 순서 유지 중복 제거

    # ---------------------------------------------------------- 상세
    def parse(self, url: str, body: str) -> RawProduct:
        ld = self._jsonld(body)

        name = ld.get("name") or select_one(body, self.sel.get("name", "")) or ""
        brand = self._brand(ld) or select_one(body, self.sel.get("brand", "")) or ""
        price = self._price(ld) or parse_price(
            select_one(body, self.sel.get("price", ""))
        )
        images = self._images(ld) or [
            urljoin(url, src)
            for src in select(body, self.sel.get("image", "img"), attr="src")
        ]
        options = [
            ProductOption(name="사이즈", value=v.strip(), stock=0)
            for v in select(body, self.sel.get("option", ""))
            if v.strip()
        ]

        return RawProduct(
            source=self.source,
            source_product_id=self._product_id(url, ld),
            source_url=url,
            name=name.strip(),
            brand=brand.strip(),
            cost_price=price,
            list_price=self._price(ld, key="highPrice") or None,
            category_path=[c for c in select(body, self.sel.get("breadcrumb", "")) if c],
            image_urls=list(dict.fromkeys(images))[:10],
            options=options,
            description_html=select_one(body, self.sel.get("description", "")) or "",
            attributes={"원본사이트": self.base_url},
        )

    # ---------------------------------------------------------- 보조
    def _jsonld(self, body: str) -> dict[str, Any]:
        """대부분의 커머스 사이트는 schema.org Product JSON-LD 를 심어 둔다.
        CSS 셀렉터보다 사이트 개편에 훨씬 강해서 이걸 1순위로 본다."""
        for chunk in JSONLD_RE.findall(body):
            try:
                data = json.loads(chunk.strip())
            except Exception:
                continue
            for node in (data if isinstance(data, list) else [data]):
                if isinstance(node, dict) and "Product" in str(node.get("@type", "")):
                    return node
        return {}

    def _brand(self, ld: dict) -> str:
        brand = ld.get("brand")
        if isinstance(brand, dict):
            return str(brand.get("name", ""))
        return str(brand or "")

    def _price(self, ld: dict, key: str = "price") -> int:
        offers = ld.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        val = offers.get(key) or (offers.get("lowPrice") if key == "price" else None)
        try:
            return int(float(val))
        except (TypeError, ValueError):
            return 0

    def _images(self, ld: dict) -> list[str]:
        img = ld.get("image")
        if isinstance(img, str):
            return [img]
        if isinstance(img, list):
            return [i for i in img if isinstance(i, str)]
        return []

    def _product_id(self, url: str, ld: dict) -> str:
        for key in ("sku", "productID", "mpn"):
            if ld.get(key):
                return str(ld[key])
        if (m := re.search(r"(?:prdtNo|goodsNo|productNo|prdNo)=([\w-]+)", url)):
            return m.group(1)
        return re.sub(r"\W+", "-", url.rsplit("/", 1)[-1])[:64] or "unknown"
