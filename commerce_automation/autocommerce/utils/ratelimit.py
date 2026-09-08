"""토큰 버킷 레이트 리미터.

크롤링 대상 서버와 마켓 OpenAPI 양쪽 모두 호출 한도가 있다.
도메인/엔드포인트 단위로 인스턴스를 나눠 쓴다.
"""

from __future__ import annotations

import threading
import time


class RateLimiter:
    def __init__(self, rate_per_sec: float, burst: int | None = None) -> None:
        if rate_per_sec <= 0:
            raise ValueError("rate_per_sec must be > 0")
        self.rate = rate_per_sec
        self.capacity = burst if burst is not None else max(1, int(rate_per_sec))
        self._tokens = float(self.capacity)
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, tokens: int = 1) -> float:
        """토큰이 찰 때까지 블로킹. 실제로 잠든 시간을 초 단위로 반환."""
        waited = 0.0
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    self.capacity, self._tokens + (now - self._last) * self.rate
                )
                self._last = now
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return waited
                deficit = tokens - self._tokens
                sleep_for = deficit / self.rate
            time.sleep(sleep_for)
            waited += sleep_for


class DomainLimiter:
    """도메인별 리미터 풀."""

    def __init__(self, default_rate: float = 0.5) -> None:
        self.default_rate = default_rate
        self._pool: dict[str, RateLimiter] = {}
        self._lock = threading.Lock()

    def for_domain(self, domain: str, rate: float | None = None) -> RateLimiter:
        with self._lock:
            if domain not in self._pool:
                self._pool[domain] = RateLimiter(rate or self.default_rate)
            return self._pool[domain]
