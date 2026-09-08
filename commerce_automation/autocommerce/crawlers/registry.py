"""소스 이름 -> 크롤러 클래스 매핑."""

from __future__ import annotations

from typing import Any, Type

from ..utils.http import HttpClient
from ..utils.robots import RobotsGate
from .abcmart import AbcMartCrawler
from .base import BaseCrawler
from .fixture import FixtureCrawler

REGISTRY: dict[str, Type[BaseCrawler]] = {
    FixtureCrawler.source: FixtureCrawler,
    AbcMartCrawler.source: AbcMartCrawler,
}


def register(cls: Type[BaseCrawler]) -> Type[BaseCrawler]:
    """새 소스 추가용 데코레이터."""
    REGISTRY[cls.source] = cls
    return cls


def build(source: str, settings) -> BaseCrawler:
    if source not in REGISTRY:
        raise KeyError(
            f"알 수 없는 소스 '{source}'. 사용 가능: {', '.join(sorted(REGISTRY))}"
        )
    ccfg: dict[str, Any] = settings.section("crawler")
    src_cfg = (ccfg.get("sources") or {}).get(source, {})
    ua = ccfg.get("user_agent", "autocommerce/0.1")
    http = HttpClient(
        timeout=float(ccfg.get("timeout", 20)),
        user_agent=ua,
        default_rate=float(ccfg.get("rate_per_sec", 0.5)),
    )
    robots = RobotsGate(ua, enabled=bool(ccfg.get("respect_robots", True)))
    return REGISTRY[source](src_cfg, http, robots)
