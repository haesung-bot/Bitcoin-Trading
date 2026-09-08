"""재시도/백오프가 들어간 HTTP 클라이언트.

- 5xx, 429, 네트워크 오류만 재시도한다 (4xx 는 재시도해도 소용없음).
- 429 의 Retry-After 헤더를 존중한다.
- 모든 요청은 RateLimiter 를 통과한다.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any
from urllib.parse import urlparse

import requests

from .logging import get
from .ratelimit import DomainLimiter

log = get("http")

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


class HttpError(RuntimeError):
    def __init__(self, status: int, body: str, url: str) -> None:
        super().__init__(f"HTTP {status} for {url}: {body[:400]}")
        self.status = status
        self.body = body
        self.url = url


class HttpClient:
    def __init__(
        self,
        *,
        timeout: float = 20.0,
        max_retries: int = 4,
        user_agent: str = "autocommerce/0.1 (+contact: set in config)",
        default_rate: float = 0.5,
        verify: bool | str = True,
    ) -> None:
        self.timeout = timeout
        self.max_retries = max_retries
        self.verify = verify
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self.limiter = DomainLimiter(default_rate)
        self._rates: dict[str, float] = {}

    def set_rate(self, domain: str, rate_per_sec: float) -> None:
        self._rates[domain] = rate_per_sec

    def request(self, method: str, url: str, **kw: Any) -> requests.Response:
        domain = urlparse(url).netloc
        limiter = self.limiter.for_domain(domain, self._rates.get(domain))
        kw.setdefault("timeout", self.timeout)
        kw.setdefault("verify", self.verify)

        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            limiter.acquire()
            try:
                resp = self.session.request(method, url, **kw)
            except requests.RequestException as exc:
                last_exc = exc
                if attempt == self.max_retries:
                    raise
                self._sleep(attempt, None)
                continue

            if resp.status_code in RETRYABLE_STATUS and attempt < self.max_retries:
                self._sleep(attempt, resp.headers.get("Retry-After"))
                continue
            if resp.status_code >= 400:
                raise HttpError(resp.status_code, resp.text, url)
            return resp

        raise last_exc or RuntimeError("unreachable")

    def get(self, url: str, **kw: Any) -> requests.Response:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw: Any) -> requests.Response:
        return self.request("POST", url, **kw)

    def get_json(self, url: str, **kw: Any) -> Any:
        return self.get(url, **kw).json()

    def post_json(self, url: str, **kw: Any) -> Any:
        return self.post(url, **kw).json()

    def download(self, url: str, dest: str, **kw: Any) -> str:
        resp = self.request("GET", url, stream=True, **kw)
        with open(dest, "wb") as fh:
            for chunk in resp.iter_content(65536):
                fh.write(chunk)
        return dest

    def _sleep(self, attempt: int, retry_after: str | None) -> None:
        if retry_after:
            try:
                delay = float(retry_after)
            except ValueError:
                delay = 2.0 ** attempt
        else:
            delay = 2.0 ** attempt
        delay += random.uniform(0, 0.5)  # thundering herd 방지
        log.log(logging.WARNING, f"재시도 대기 {delay:.1f}s (attempt={attempt + 1})")
        time.sleep(delay)
