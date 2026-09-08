"""파이프라인 전 구간에서 오가는 데이터 계약(Data Contract).

각 단계는 앞 단계의 산출물을 입력으로 받아 다음 산출물을 만든다.

    RawProduct  ->  PricedProduct  ->  MediaSet  ->  Listing  ->  ListingResult
      (1)             (2)              (3)          (4)
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Stage(str, Enum):
    """파이프라인 단계. storage 의 상태 머신 키로도 쓰인다."""

    CRAWLED = "crawled"
    PRICED = "priced"
    IMAGED = "imaged"
    LISTED = "listed"
    FAILED = "failed"


@dataclass
class Money:
    """원화 정수 금액. 소수점 오차를 막기 위해 int(KRW)만 취급한다."""

    amount: int
    currency: str = "KRW"

    def __post_init__(self) -> None:
        self.amount = int(round(self.amount))

    def __add__(self, other: "Money") -> "Money":
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        return Money(self.amount - other.amount, self.currency)

    def __str__(self) -> str:  # pragma: no cover - 표시용
        return f"{self.amount:,}원"


# ---------------------------------------------------------------- 1. 크롤링
@dataclass
class ProductOption:
    """사이즈/색상 등 단일 판매 옵션."""

    name: str                      # 예: "사이즈"
    value: str                     # 예: "270"
    stock: int = 0
    extra_price: int = 0           # 옵션 추가금 (KRW)
    seller_option_code: str | None = None


@dataclass
class RawProduct:
    """크롤러 산출물. 가공 전 원본 그대로."""

    source: str                    # "abcmart" | "brand_official" | "fixture"
    source_product_id: str         # 원본 사이트의 상품 코드
    source_url: str
    name: str                      # 원본 상품명
    brand: str
    cost_price: int                # 실매입가(VAT 포함, KRW)
    list_price: int | None = None  # 정가
    category_path: list[str] = field(default_factory=list)
    image_urls: list[str] = field(default_factory=list)
    options: list[ProductOption] = field(default_factory=list)
    description_html: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)  # 소재/원산지/제조사 등
    crawled_at: str = field(default_factory=_now)

    @property
    def uid(self) -> str:
        """소스+상품코드 기반 안정적 고유키. 중복 등록 방지의 기준."""
        return f"{self.source}:{self.source_product_id}"

    @property
    def content_hash(self) -> str:
        """가격/재고/이미지 변동 감지용 해시."""
        payload = json.dumps(
            {
                "cost": self.cost_price,
                "images": sorted(self.image_urls),
                "options": sorted((o.name, o.value, o.stock) for o in self.options),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


# ------------------------------------------------- 2. 마진 계산 / SEO 키워드
@dataclass
class PriceBreakdown:
    """판매가가 '왜' 그 숫자인지 전부 드러내는 내역서.

    자동화에서 가장 위험한 게 값이 틀린 줄 모르고 대량 등록되는 것이라,
    모든 항목을 저장해 두고 등록 전에 검수할 수 있게 한다.

    sell_price 는 마켓에 노출되는 상품가, total_revenue 는 배송비까지 합친
    실제 매출이다. 수수료와 마진율은 total_revenue 기준으로 계산된다 —
    마켓이 배송비 포함 결제금액에 수수료를 매기기 때문.
    """

    sell_price: int                # 마켓 노출 상품가 (VAT 포함)
    shipping_charge: int           # 고객에게 청구하는 배송비 (무료배송이면 0)
    total_revenue: int             # sell_price + shipping_charge
    cost_price: int
    inbound_shipping: int          # 매입 배송비 (공홈 -> 나)
    shipping_cost: int             # 실제 택배 단가 (판매자가 택배사에 지불)
    packaging: int
    commission_rate: float         # 마켓 판매수수료 + 결제 + 매출연동 + 광고 합산
    commission_amount: int
    vat_payable: int
    net_profit: int
    margin_rate: float             # net_profit / total_revenue
    policy_name: str
    shipping_mode: str = "free"    # free | paid | conditional
    free_ship_over: int = 0        # conditional 일 때 무료 기준액
    warnings: list[str] = field(default_factory=list)


@dataclass
class SeoContent:
    """마켓 노출용 텍스트 자산."""

    title: str                     # 마켓 상품명 (길이 제한 적용 후)
    tags: list[str] = field(default_factory=list)
    search_keywords: list[str] = field(default_factory=list)
    dropped_terms: list[str] = field(default_factory=list)  # 금칙어/중복으로 제거된 것
    warnings: list[str] = field(default_factory=list)


@dataclass
class PricedProduct:
    raw: RawProduct
    price: PriceBreakdown
    seo: SeoContent
    priced_at: str = field(default_factory=_now)


# ------------------------------------------------- 3. AI 이미지 변환
@dataclass
class MediaAsset:
    """이미지 1장의 원본 -> 가공본 계보(provenance)."""

    role: str                      # "main" | "sub" | "detail"
    source_url: str
    local_path: str | None = None  # 정규화 산출물 경로
    remote_url: str | None = None  # 마켓 업로드 후 URL
    width: int | None = None
    height: int | None = None
    transform: str = "normalize"   # "normalize" | "flux:<model>" | "passthrough"
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass
class MediaSet:
    product_uid: str
    assets: list[MediaAsset] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def main(self) -> MediaAsset | None:
        return next((a for a in self.assets if a.role == "main"), None)


# ------------------------------------------------- 4. 마켓 등록
@dataclass
class Listing:
    """마켓에 보내기 직전의 중립 표현. 어댑터가 이걸 마켓 스키마로 번역한다."""

    product_uid: str
    market: str                    # "naver" | "coupang"
    title: str
    sell_price: int
    stock: int
    category_code: str
    brand: str
    manufacturer: str
    origin: str
    images: list[str]              # 업로드된 이미지 URL
    options: list[ProductOption]
    detail_html: str
    tags: list[str]
    attributes: dict[str, Any] = field(default_factory=dict)
    # 배송비 정책 — 마진 계산이 전제한 값과 마켓 등록값이 어긋나면 안 되므로
    # PriceBreakdown 에서 그대로 실어 나른다.
    shipping_mode: str = "free"    # free | paid | conditional
    shipping_charge: int = 0       # 고객 청구 배송비
    free_ship_over: int = 0        # conditional 일 때 무료 기준액


@dataclass
class ListingResult:
    product_uid: str
    market: str
    ok: bool
    market_product_id: str | None = None
    url: str | None = None
    error: str | None = None
    request_payload: dict[str, Any] = field(default_factory=dict)
    response: dict[str, Any] = field(default_factory=dict)
    dry_run: bool = False
    at: str = field(default_factory=_now)


# ------------------------------------------------- 5. 주문 / 1-Click 확인 구매
class PurchaseState(str, Enum):
    PENDING = "pending"            # 확인 대기 (사람이 승인해야 함)
    APPROVED = "approved"          # 사용자가 승인 -> 결제는 사용자가 수행
    ORDERED = "ordered"            # 공홈 주문번호 확보
    REJECTED = "rejected"
    FAILED = "failed"


@dataclass
class PurchaseTask:
    """마켓 주문 1건 -> 공홈 구매 1건으로 이어지는 '확인 구매' 작업.

    결제 승인은 자동화하지 않는다. 시스템은 주문 정보를 정규화하고
    구매 페이지 딥링크와 입력값을 준비해 두는 데까지만 책임진다.
    """

    task_id: str
    market: str
    market_order_id: str
    product_uid: str
    option_value: str
    quantity: int
    buyer_paid: int                # 고객이 결제한 금액
    expected_cost: int             # 등록 시점에 계산한 매입가
    receiver: dict[str, Any]       # 이름/연락처/주소 (개인정보: 저장소 접근 통제 필요)
    source_url: str
    deeplink: str | None = None
    state: str = PurchaseState.PENDING.value
    cost_drift: int = 0            # 실제 매입가 - 예상 매입가
    note: str = ""
    created_at: str = field(default_factory=_now)


def to_dict(obj: Any) -> dict[str, Any]:
    """dataclass -> dict (JSON 직렬화용)."""
    return asdict(obj)
