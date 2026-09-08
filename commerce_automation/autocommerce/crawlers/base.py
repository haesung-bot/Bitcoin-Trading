"""크롤러 공통 규약.

새 소스를 붙일 때 구현해야 하는 건 두 개뿐이다.
    - discover(**params) -> 상품 상세 URL 목록
    - parse(url, html)   -> RawProduct

robots.txt 준수, 레이트 리밋, 재시도, 파싱 실패 격리는 베이스가 처리한다.
파싱 실패 1건이 배치 전체를 죽이지 않도록 상품 단위로 예외를 가둔다.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Iterator

from ..models import RawProduct
from ..utils.http import HttpClient
from ..utils.logging import get
from ..utils.robots import RobotsGate

log = get("crawler")


class CrawlBlocked(RuntimeError):
    """robots.txt 가 막은 경로. 우회하지 않고 그대로 실패시킨다."""


@dataclass
class CrawlReport:
    source: str
    requested: int = 0
    parsed: int = 0
    blocked: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)  # (url, 사유)

    def summary(self) -> str:
        return (
            f"[{self.source}] 요청 {self.requested} / 성공 {self.parsed} / "
            f"차단 {self.blocked} / 실패 {len(self.failed)}"
        )


class BaseCrawler(abc.ABC):
    source: str = "base"

    def __init__(self, cfg: dict[str, Any], http: HttpClient,
                 robots: RobotsGate) -> None:
        self.cfg = cfg or {}
        self.http = http
        self.robots = robots
        self.report = CrawlReport(source=self.source)

    # -------------------------------------------------- 구현 필요
    @abc.abstractmethod
    def discover(self, **params: Any) -> list[str]:
        """수집 대상 상품 상세 URL 목록."""

    @abc.abstractmethod
    def parse(self, url: str, body: str) -> RawProduct:
        """상세 페이지 HTML -> RawProduct."""

    # -------------------------------------------------- 공통 실행
    def fetch(self, url: str) -> str:
        if not self.robots.allowed(url):
            self.report.blocked += 1
            raise CrawlBlocked(f"robots.txt 가 차단한 URL: {url}")
        self.report.requested += 1
        return self.http.get(url).text

    def crawl(self, limit: int | None = None, **params: Any) -> Iterator[RawProduct]:
        urls = self.discover(**params)
        if limit:
            urls = urls[:limit]
        log.info(f"[{self.source}] 상세 페이지 {len(urls)}건 수집 시작")
        for url in urls:
            try:
                product = self.parse(url, self.fetch(url))
            except CrawlBlocked as exc:
                log.warning(str(exc))
                continue
            except Exception as exc:  # 상품 1건 실패가 배치를 죽이지 않게
                log.warning(f"[{self.source}] 파싱 실패 {url}: {exc}")
                self.report.failed.append((url, f"{type(exc).__name__}: {exc}"))
                continue
            if not self.validate(product):
                continue
            self.report.parsed += 1
            yield product

    def validate(self, product: RawProduct) -> bool:
        """가격 0원, 이름 없음 같은 명백한 오파싱을 여기서 잘라낸다."""
        problems = []
        if not product.name.strip():
            problems.append("상품명 없음")
        if product.cost_price <= 0:
            problems.append(f"가격 이상({product.cost_price})")
        if not product.image_urls:
            problems.append("이미지 없음")
        if problems:
            log.warning(
                f"[{self.source}] 검증 탈락 {product.source_url}: {', '.join(problems)}"
            )
            self.report.failed.append((product.source_url, ", ".join(problems)))
            return False
        return True
