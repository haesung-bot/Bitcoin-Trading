"""오픈마켓 어댑터 공통 규약.

파이프라인은 마켓별 스키마를 몰라야 한다. 중립 표현 `Listing` 만 만들고,
각 어댑터가 자기 마켓의 요청 바디로 번역한다.

dry_run
-------
기본값은 dry_run=True 다. 자동화에서 가장 비싼 실수는 '잘못된 값으로
수백 건이 실제 등록되는 것'이라, 페이로드만 만들어 저장하고 전송은 하지 않는
모드를 1급 시민으로 둔다. 검수 후 --live 로 전환한다.
"""

from __future__ import annotations

import abc
from typing import Any

from pathlib import Path

from ..models import Listing, ListingResult, MediaSet, PurchaseTask
from ..utils.http import HttpClient
from ..utils.logging import get

log = get("market")


class MarketError(RuntimeError):
    pass


class CredentialsMissing(MarketError):
    pass


class MarketAdapter(abc.ABC):
    market: str = "base"
    #  마켓별 하드 제약. 등록 전에 여기서 걸러야 API 400 을 안 맞는다.
    max_title_len: int = 100
    max_images: int = 10

    def __init__(self, cfg: dict[str, Any], *, dry_run: bool = True,
                 http: HttpClient | None = None) -> None:
        self.cfg = cfg or {}
        self.dry_run = dry_run
        self.http = http or HttpClient(default_rate=2.0)

    # -------------------------------------------------- 구현 필요
    @abc.abstractmethod
    def required_credentials(self) -> list[str]:
        """설정에서 반드시 채워져야 하는 키 목록."""

    @abc.abstractmethod
    def build_payload(self, listing: Listing) -> dict[str, Any]:
        """중립 Listing -> 마켓 API 요청 바디."""

    @abc.abstractmethod
    def _send(self, payload: dict[str, Any]) -> dict[str, Any]:
        """실제 등록 호출 (dry_run 이 아닐 때만 불린다)."""

    @abc.abstractmethod
    def fetch_orders(self, since: str) -> list[PurchaseTask]:
        """신규 주문 조회 -> 확인 구매 작업으로 정규화."""

    # -------------------------------------------------- 이미지
    def upload_image(self, local_path: str) -> str:
        """로컬 파일 -> 마켓이 접근 가능한 URL. 마켓별로 구현한다."""
        raise NotImplementedError(
            f"[{self.market}] 이미지 업로드가 구현되지 않았습니다."
        )

    def publish_images(self, media: MediaSet) -> list[str]:
        """등록에 쓸 이미지 URL 목록을 만든다.

        dry_run 에서는 실제로 업로드하지 않고 `dryrun://` 플레이스홀더를 넣는다.
        이렇게 해야 이미지 업로드 없이도 나머지 페이로드를 끝까지 검증할 수 있다.
        MediaSet 은 여러 마켓이 공유하므로 여기서 변형하지 않는다.
        """
        if not self.dry_run:
            self.check_credentials()   # 업로드 전에 먼저 막아야 헛수고를 안 한다
        urls: list[str] = []
        for asset in media.assets:
            if asset.remote_url:              # 이미 공개 URL 이 있으면 그대로
                urls.append(asset.remote_url)
                continue
            if not asset.local_path:
                continue
            if self.dry_run:
                name = Path(asset.local_path).name
                urls.append(f"dryrun://{self.market}/{name}")
            else:
                urls.append(self.upload_image(asset.local_path))
        return urls

    # -------------------------------------------------- 공통
    def check_credentials(self) -> None:
        missing = [k for k in self.required_credentials() if not self.cfg.get(k)]
        if missing:
            raise CredentialsMissing(
                f"[{self.market}] 자격증명 누락: {', '.join(missing)} — "
                f".env 와 settings.yaml 을 확인하세요."
            )

    def preflight(self, listing: Listing) -> list[str]:
        """전송 전 마켓 제약 검사. 반환값이 비어 있어야 등록 가능."""
        errs: list[str] = []
        if len(listing.title) > self.max_title_len:
            errs.append(
                f"상품명 {len(listing.title)}자 > 허용 {self.max_title_len}자"
            )
        if not listing.images:
            errs.append("이미지가 없습니다")
        if len(listing.images) > self.max_images:
            errs.append(f"이미지 {len(listing.images)}장 > 허용 {self.max_images}장")
        if listing.sell_price <= 0:
            errs.append(f"판매가가 유효하지 않습니다: {listing.sell_price}")
        if not listing.category_code:
            errs.append("카테고리 코드가 비었습니다")
        if listing.stock <= 0:
            errs.append("재고가 0입니다")
        return errs

    def create_listing(self, listing: Listing) -> ListingResult:
        payload = self.build_payload(listing)

        if (errs := self.preflight(listing)):
            return ListingResult(
                product_uid=listing.product_uid, market=self.market, ok=False,
                error="; ".join(errs), request_payload=payload, dry_run=self.dry_run,
            )

        if self.dry_run:
            log.info(f"[DRY-RUN] {self.market} 등록 시뮬레이션: {listing.title}")
            return ListingResult(
                product_uid=listing.product_uid, market=self.market, ok=True,
                market_product_id=None, request_payload=payload, dry_run=True,
                response={"note": "dry_run — 실제 전송하지 않음"},
            )

        try:
            self.check_credentials()
            resp = self._send(payload)
        except Exception as exc:
            return ListingResult(
                product_uid=listing.product_uid, market=self.market, ok=False,
                error=f"{type(exc).__name__}: {exc}", request_payload=payload,
            )

        return ListingResult(
            product_uid=listing.product_uid, market=self.market, ok=True,
            market_product_id=self.extract_product_id(resp),
            request_payload=payload, response=resp,
        )

    def extract_product_id(self, resp: dict[str, Any]) -> str | None:
        for key in ("productId", "sellerProductId", "originProductNo", "no", "id"):
            if key in resp:
                return str(resp[key])
        data = resp.get("data")
        if isinstance(data, dict):
            return self.extract_product_id(data)
        if isinstance(data, (str, int)):
            return str(data)
        return None
