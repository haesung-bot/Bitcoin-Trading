"""오프라인 픽스처 크롤러.

실제 사이트를 때리지 않고 파이프라인 전 구간(가격/이미지/등록)을 돌려보기 위한 소스.
개발·테스트·데모의 기본값이며, `crawler.sources.fixture.path` 의 JSON 을 읽는다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..models import ProductOption, RawProduct
from .base import BaseCrawler


class FixtureCrawler(BaseCrawler):
    source = "fixture"

    def discover(self, **params: Any) -> list[str]:
        path = Path(self.cfg.get("path", "./data/fixtures/abcmart_sample.json"))
        if not path.exists():
            raise FileNotFoundError(f"픽스처 파일이 없습니다: {path}")
        self._items = json.loads(path.read_text(encoding="utf-8"))
        return [item["source_url"] for item in self._items]

    def fetch(self, url: str) -> str:
        self.report.requested += 1
        return json.dumps(next(i for i in self._items if i["source_url"] == url))

    def parse(self, url: str, body: str) -> RawProduct:
        item = json.loads(body)
        return RawProduct(
            source=self.source,
            source_product_id=item["source_product_id"],
            source_url=item["source_url"],
            name=item["name"],
            brand=item.get("brand", ""),
            cost_price=int(item["cost_price"]),
            list_price=item.get("list_price"),
            category_path=item.get("category_path", []),
            image_urls=item.get("image_urls", []),
            options=[ProductOption(**o) for o in item.get("options", [])],
            description_html=item.get("description_html", ""),
            attributes=item.get("attributes", {}),
        )
