"""Replicate(FLUX) 생성형 이미지 변환 클라이언트.

Replicate 는 예측(prediction)을 만들고 상태를 폴링하는 비동기 API다.
  POST /v1/models/{owner}/{name}/predictions   (공식 모델)
  POST /v1/predictions  {"version": "<hash>"}  (버전 고정 모델)
  GET  <urls.get>                              (상태 폴링)

`Prefer: wait` 헤더를 붙이면 짧은 작업은 동기로 끝나기도 하지만,
항상 완료를 보장하지 않으므로 폴링 경로를 그대로 유지한다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from ..utils.http import HttpClient
from ..utils.logging import get

log = get("imaging.replicate")

API = "https://api.replicate.com/v1"
TERMINAL = {"succeeded", "failed", "canceled"}


class ReplicateError(RuntimeError):
    pass


@dataclass
class ReplicateConfig:
    api_token: str = ""
    model: str = "black-forest-labs/flux-kontext-pro"
    version: str | None = None          # 버전 해시를 쓰면 model 대신 이걸 사용
    prompt: str = (
        "clean studio product photo on pure white seamless background, centered, "
        "soft even lighting, no text, no watermark, no logo overlay"
    )
    poll_interval: float = 2.0
    timeout: float = 180.0
    extra_input: dict[str, Any] = None

    def __post_init__(self) -> None:
        self.extra_input = self.extra_input or {}

    @classmethod
    def from_config(cls, cfg: dict) -> "ReplicateConfig":
        known = {"api_token", "model", "version", "prompt",
                 "poll_interval", "timeout", "extra_input"}
        return cls(**{k: v for k, v in (cfg or {}).items() if k in known})


class ReplicateClient:
    def __init__(self, cfg: ReplicateConfig, http: HttpClient | None = None) -> None:
        self.cfg = cfg
        self.http = http or HttpClient(default_rate=2.0)
        if not cfg.api_token:
            raise ReplicateError(
                "REPLICATE_API_TOKEN 이 비어 있습니다. .env 를 확인하세요."
            )

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.cfg.api_token}",
            "Content-Type": "application/json",
        }

    def transform(self, image_url: str, *, prompt: str | None = None,
                  **overrides: Any) -> str:
        """이미지 URL 하나를 변환하고 결과 이미지 URL 을 반환한다."""
        payload = {
            "input": {
                "prompt": prompt or self.cfg.prompt,
                "input_image": image_url,
                "output_format": "jpg",
                **self.cfg.extra_input,
                **overrides,
            }
        }
        if self.cfg.version:
            url = f"{API}/predictions"
            payload["version"] = self.cfg.version
        else:
            owner, _, name = self.cfg.model.partition("/")
            if not name:
                raise ReplicateError(
                    f"model 은 'owner/name' 형식이어야 합니다: {self.cfg.model}"
                )
            url = f"{API}/models/{owner}/{name}/predictions"

        pred = self.http.post_json(url, json=payload, headers=self._headers)
        return self._wait(pred)

    def _wait(self, pred: dict[str, Any]) -> str:
        get_url = (pred.get("urls") or {}).get("get")
        if not get_url:
            raise ReplicateError(f"예측 URL 이 응답에 없습니다: {pred}")

        deadline = time.monotonic() + self.cfg.timeout
        status = pred.get("status", "starting")
        while status not in TERMINAL:
            if time.monotonic() > deadline:
                raise ReplicateError(
                    f"변환 타임아웃({self.cfg.timeout}s) — prediction={pred.get('id')}"
                )
            time.sleep(self.cfg.poll_interval)
            pred = self.http.get_json(get_url, headers=self._headers)
            status = pred.get("status", "")

        if status != "succeeded":
            raise ReplicateError(
                f"변환 실패(status={status}): {pred.get('error') or pred.get('logs', '')[-300:]}"
            )
        return self._extract(pred.get("output"))

    @staticmethod
    def _extract(output: Any) -> str:
        if isinstance(output, str):
            return output
        if isinstance(output, list) and output:
            first = output[0]
            if isinstance(first, str):
                return first
        raise ReplicateError(f"예상치 못한 output 형식: {type(output).__name__}")
