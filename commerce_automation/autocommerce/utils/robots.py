"""robots.txt 준수 체커.

크롤러가 대상 사이트의 robots.txt 를 무시하면 차단은 물론 법적 리스크가 생긴다.
기본값은 '준수'이며, 설정에서 명시적으로 끄지 않는 한 우회하지 않는다.
"""

from __future__ import annotations

import urllib.robotparser as robotparser
from urllib.parse import urljoin, urlparse

from .logging import get

log = get("robots")


class RobotsGate:
    def __init__(self, user_agent: str, enabled: bool = True) -> None:
        self.user_agent = user_agent
        self.enabled = enabled
        self._cache: dict[str, robotparser.RobotFileParser | None] = {}

    def allowed(self, url: str) -> bool:
        if not self.enabled:
            return True
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._cache:
            self._cache[origin] = self._load(origin)
        rp = self._cache[origin]
        if rp is None:  # robots.txt 를 못 읽으면 보수적으로 허용하되 경고
            log.warning(f"robots.txt 확인 실패, 기본 허용: {origin}")
            return True
        return rp.can_fetch(self.user_agent, url)

    def crawl_delay(self, url: str) -> float | None:
        if not self.enabled:
            return None
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        rp = self._cache.get(origin)
        if rp is None:
            return None
        try:
            delay = rp.crawl_delay(self.user_agent)
            return float(delay) if delay else None
        except Exception:
            return None

    def _load(self, origin: str) -> robotparser.RobotFileParser | None:
        rp = robotparser.RobotFileParser()
        rp.set_url(urljoin(origin, "/robots.txt"))
        try:
            rp.read()
            return rp
        except Exception as exc:
            log.warning(f"robots.txt 읽기 오류 {origin}: {exc}")
            return None
