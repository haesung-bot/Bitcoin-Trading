"""이미지 파이프라인 오케스트레이션.

  원본 URL  ->  다운로드  ->  [생성형 변환(선택)]  ->  규격화  ->  MediaAsset

저작권 가드
----------
상품 이미지는 브랜드/판매처의 저작물이다. 무단 사용은 물론이고
'AI 로 변형해서 중복 검출을 피하는' 용도는 마켓 정책 위반이자 저작권 문제로
계정 정지 사유가 된다. 그래서 `imaging.licensing.require_source_license` 가
켜져 있으면 `licensed_sources` 에 등록된(= 사용 권한을 확인한) 소스만 처리하고,
나머지는 건너뛴 뒤 사유를 경고로 남긴다.

모든 산출물은 provenance(원본 URL, 변환 종류, 시각)를 함께 저장한다.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..models import MediaAsset, MediaSet, RawProduct
from ..utils.http import HttpClient
from ..utils.logging import get
from .normalize import NormalizeSpec, normalize_file, output_name
from .replicate_client import ReplicateClient, ReplicateConfig, ReplicateError

log = get("imaging")


class LicenseBlocked(RuntimeError):
    """사용 권한이 확인되지 않은 소스의 이미지."""


class ImagePipeline:
    def __init__(self, cfg: dict[str, Any], workdir: str | Path,
                 http: HttpClient | None = None) -> None:
        self.cfg = cfg or {}
        self.enabled = bool(self.cfg.get("enabled", True))
        self.provider = str(self.cfg.get("provider", "local"))
        self.max_images = int(self.cfg.get("max_images", 5))
        self.spec = NormalizeSpec.from_config(self.cfg)
        self.http = http or HttpClient(default_rate=2.0)

        self.root = Path(workdir) / "images"
        (self.root / "src").mkdir(parents=True, exist_ok=True)
        (self.root / "out").mkdir(parents=True, exist_ok=True)

        lic = self.cfg.get("licensing", {}) or {}
        self.require_license = bool(lic.get("require_source_license", True))
        self.licensed_sources = set(lic.get("licensed_sources", []) or [])

        self._replicate: ReplicateClient | None = None

    # ------------------------------------------------------------------
    def license_ok(self, source: str) -> bool:
        return (not self.require_license) or source in self.licensed_sources

    def replicate(self) -> ReplicateClient:
        if self._replicate is None:
            self._replicate = ReplicateClient(
                ReplicateConfig.from_config(self.cfg.get("replicate", {})), self.http
            )
        return self._replicate

    # ------------------------------------------------------------------
    def process(self, product: RawProduct) -> MediaSet:
        media = MediaSet(product_uid=product.uid)

        if not self.enabled:
            media.warnings.append("imaging.enabled=false — 원본 URL 을 그대로 사용")
            media.assets = [
                MediaAsset(role=("main" if i == 0 else "sub"), source_url=u,
                           remote_url=u, transform="passthrough")
                for i, u in enumerate(product.image_urls[: self.max_images])
            ]
            return media

        if not self.license_ok(product.source):
            media.warnings.append(
                f"소스 '{product.source}' 는 이미지 사용 권한이 확인되지 않아 "
                f"변환을 건너뜁니다. 권한 확인 후 "
                f"imaging.licensing.licensed_sources 에 추가하세요."
            )
            return media

        for idx, url in enumerate(product.image_urls[: self.max_images]):
            role = "main" if idx == 0 else "sub"
            try:
                media.assets.append(self._one(product, url, role))
            except Exception as exc:
                log.warning(f"이미지 처리 실패 {url}: {exc}")
                media.warnings.append(f"{role} 이미지 실패: {exc}")

        if not media.main:
            media.warnings.append("대표이미지 생성 실패 — 등록 보류 대상")
        return media

    # ------------------------------------------------------------------
    def _one(self, product: RawProduct, url: str, role: str) -> MediaAsset:
        transform = "normalize"
        working_url = url

        # 1) 생성형 변환 (대표이미지에만 적용해 비용을 줄인다)
        if self.provider == "replicate" and role == "main":
            try:
                working_url = self.replicate().transform(url)
                transform = f"flux:{self.cfg.get('replicate', {}).get('model', '?')}"
            except ReplicateError as exc:
                log.warning(f"Replicate 변환 실패, 원본으로 진행: {exc}")

        # 2) 확보 (원격이면 다운로드, 로컬/file:// 이면 복사 — 픽스처 테스트용)
        src_path = self.root / "src" / self._cache_name(working_url)
        if not src_path.exists():
            self._acquire(working_url, src_path)

        # 3) 규격화
        out_path = self.root / "out" / output_name(src_path, self.spec, role)
        path, w, h, warns = normalize_file(src_path, out_path, self.spec)
        for w_ in warns:
            log.warning(f"{product.uid} {role}: {w_}")

        return MediaAsset(
            role=role,
            source_url=url,
            local_path=path,
            width=w or None,
            height=h or None,
            transform=transform,
            provenance={
                "original_url": url,
                "intermediate_url": working_url if working_url != url else None,
                "source_site": product.source,
                "processed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "spec": f"{self.spec.size}px/{self.spec.fmt}/q{self.spec.quality}",
            },
        )

    def _acquire(self, url: str, dest: Path) -> None:
        if url.startswith(("http://", "https://")):
            self.http.download(url, str(dest))
            return
        local = Path(url[7:] if url.startswith("file://") else url)
        if not local.exists():
            raise FileNotFoundError(f"로컬 이미지가 없습니다: {local}")
        dest.write_bytes(local.read_bytes())

    @staticmethod
    def _cache_name(url: str) -> str:
        digest = hashlib.sha1(url.encode()).hexdigest()[:16]
        ext = Path(url.split("?")[0]).suffix.lower()
        if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            ext = ".jpg"
        return f"{digest}{ext}"
