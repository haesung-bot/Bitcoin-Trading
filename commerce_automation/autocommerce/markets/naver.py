"""네이버 커머스 API(스마트스토어) 어댑터.

인증: OAuth2 client_credentials + bcrypt 전자서명
    password = f"{client_id}_{timestamp_ms}"
    sign     = base64( bcrypt.hashpw(password, salt=client_secret) )
    POST /external/v1/oauth2/token
         ?client_id=&timestamp=&client_secret_sign=&grant_type=client_credentials&type=SELF

  · client_secret 이 bcrypt salt 로 그대로 쓰인다 (형식: $2a$10$...)
  · timestamp 는 밀리초이고 토큰 요청 시각과 5분 이상 벌어지면 거부된다
  · 발급된 access_token 은 만료 60초 전에 미리 재발급한다

상품 등록: POST /external/v2/products
    { "originProduct": {...}, "smartstoreChannelProduct": {...} }
"""

from __future__ import annotations

import base64
import time
from typing import Any

from ..models import Listing, PurchaseTask
from ..utils.logging import get
from .base import MarketAdapter, MarketError

log = get("market.naver")

TOKEN_PATH = "/external/v1/oauth2/token"
PRODUCT_PATH = "/external/v2/products"
IMAGE_PATH = "/external/v1/product-images/upload"
ORDER_CHANGED_PATH = "/external/v1/pay-order/seller/product-orders/last-changed-statuses"
ORDER_QUERY_PATH = "/external/v1/pay-order/seller/product-orders/query"

try:  # bcrypt 는 선택 의존성 (dry_run 만 쓸 거면 필요 없음)
    import bcrypt  # type: ignore

    BCRYPT = True
except ImportError:  # pragma: no cover
    bcrypt = None  # type: ignore
    BCRYPT = False


def client_secret_sign(client_id: str, client_secret: str,
                       timestamp_ms: int | None = None) -> tuple[str, int]:
    """(전자서명, 사용한 timestamp) 반환."""
    if not BCRYPT:
        raise MarketError(
            "네이버 인증에는 bcrypt 가 필요합니다: pip install bcrypt"
        )
    ts = timestamp_ms if timestamp_ms is not None else int(time.time() * 1000)
    password = f"{client_id}_{ts}"
    try:
        hashed = bcrypt.hashpw(password.encode("utf-8"), client_secret.encode("utf-8"))
    except ValueError as exc:
        # client_secret 은 bcrypt salt 그대로다 ($2a$10$... 22자 인코딩).
        # 값을 잘라 붙였거나 따옴표가 섞이면 여기서 걸린다.
        raise MarketError(
            f"NAVER_CLIENT_SECRET 이 bcrypt salt 형식이 아닙니다 "
            f"($2a$..로 시작하는 29자). 커머스API 센터에서 발급받은 값을 "
            f"공백/따옴표 없이 그대로 넣으세요. (원인: {exc})"
        ) from exc
    return base64.b64encode(hashed).decode("utf-8"), ts


class NaverAdapter(MarketAdapter):
    market = "naver"
    max_title_len = 100
    max_images = 10

    def __init__(self, *args: Any, **kw: Any) -> None:
        super().__init__(*args, **kw)
        self._token: str | None = None
        self._token_exp: float = 0.0

    def required_credentials(self) -> list[str]:
        return ["client_id", "client_secret"]

    @property
    def base_url(self) -> str:
        return self.cfg.get("base_url", "https://api.commerce.naver.com").rstrip("/")

    # ------------------------------------------------------------ 토큰
    def token(self) -> str:
        if self._token and time.time() < self._token_exp:
            return self._token
        self.check_credentials()
        sign, ts = client_secret_sign(self.cfg["client_id"], self.cfg["client_secret"])
        params = {
            "client_id": self.cfg["client_id"],
            "timestamp": ts,
            "client_secret_sign": sign,
            "grant_type": "client_credentials",
            "type": "SELF",
        }
        data = self.http.post_json(
            f"{self.base_url}{TOKEN_PATH}", params=params,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if not (tok := data.get("access_token")):
            raise MarketError(f"토큰 발급 실패: {data}")
        self._token = tok
        self._token_exp = time.time() + int(data.get("expires_in", 10800)) - 60
        return tok

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token()}",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, **kw: Any) -> dict[str, Any]:
        resp = self.http.request(
            method, f"{self.base_url}{path}", headers=self._headers(), **kw
        )
        return resp.json() if resp.content else {}

    # ------------------------------------------------------------ 이미지
    def upload_image(self, local_path: str) -> str:
        """로컬 파일 -> 네이버 이미지 서버 URL. 등록 전에 반드시 거쳐야 한다."""
        with open(local_path, "rb") as fh:
            resp = self.http.request(
                "POST", f"{self.base_url}{IMAGE_PATH}",
                headers={"Authorization": f"Bearer {self.token()}"},
                files={"imageFiles": (local_path.rsplit("/", 1)[-1], fh, "image/jpeg")},
            )
        data = resp.json()
        images = data.get("images") or []
        if not images:
            raise MarketError(f"이미지 업로드 응답에 URL 이 없습니다: {data}")
        return images[0]["url"]

    # ------------------------------------------------------------ 등록
    def build_payload(self, listing: Listing) -> dict[str, Any]:
        rep, *subs = listing.images or [""]
        combos = [
            {
                "optionName1": opt.value,
                "stockQuantity": max(opt.stock, 0),
                "price": opt.extra_price,
                "usable": opt.stock > 0,
            }
            for opt in listing.options
        ]

        origin_product: dict[str, Any] = {
            "statusType": "SALE",
            "saleType": "NEW",
            "leafCategoryId": listing.category_code,
            "name": listing.title,
            "detailContent": listing.detail_html,
            "images": {
                "representativeImage": {"url": rep},
                "optionalImages": [{"url": u} for u in subs],
            },
            "saleStartDate": None,
            "saleEndDate": None,
            "salePrice": listing.sell_price,
            "stockQuantity": listing.stock,
            "deliveryInfo": {
                "deliveryType": "DELIVERY",
                "deliveryAttributeType": "NORMAL",
                "deliveryCompany": self.cfg.get("delivery_company", "CJGLS"),
                "deliveryFee": {
                    "deliveryFeeType": "FREE",
                    "deliveryFeePayType": "PREPAID",
                    "returnDeliveryFee": int(self.cfg.get("return_fee", 5000)),
                    "exchangeDeliveryFee": int(self.cfg.get("exchange_fee", 10000)),
                },
                "claimDeliveryInfo": {
                    "returnDeliveryCompanyPriorityType": "PRIMARY",
                    "returnAddressId": self.cfg.get("return_address_id", ""),
                },
            },
            "detailAttribute": {
                "naverShoppingSearchInfo": {
                    "manufacturerName": listing.manufacturer or listing.brand,
                    "brandName": listing.brand,
                },
                "afterServiceInfo": {
                    "afterServiceTelephoneNumber": self.cfg.get("after_service_tel", ""),
                    "afterServiceGuideContent": self.cfg.get(
                        "after_service_guide", "구매 후 7일 이내 교환/반품 가능합니다."
                    ),
                },
                "originAreaInfo": {
                    "originAreaCode": self.cfg.get("origin_area_code", "0200037"),
                    "content": listing.origin,
                },
                "sellerCodeInfo": {"sellerManagementCode": listing.product_uid},
                "optionInfo": ({
                    "optionCombinationSortType": "CREATE",
                    "optionCombinationGroupNames": {
                        "optionGroupName1": listing.options[0].name
                    },
                    "optionCombinations": combos,
                } if combos else {}),
                "seoInfo": {
                    "pageTitle": listing.title,
                    "metaDescription": listing.title,
                    "sellerTags": [{"text": t} for t in listing.tags[:10]],
                },
                "minorPurchasable": True,
                "productInfoProvidedNotice": {
                    "productInfoProvidedNoticeType": "SHOES",
                    "shoes": {
                        "material": listing.attributes.get("소재", "상세페이지 참조"),
                        "color": listing.attributes.get("색상", "상세페이지 참조"),
                        "size": "상세페이지 참조",
                        "manufacturer": listing.manufacturer or listing.brand,
                        "caution": "상세페이지 참조",
                        "warrantyPolicy": "상세페이지 참조",
                        "afterServiceDirector": self.cfg.get("after_service_tel", ""),
                    },
                },
            },
        }

        return {
            "originProduct": origin_product,
            "smartstoreChannelProduct": {
                "naverShoppingRegistration": True,
                "channelProductDisplayStatusType": "ON",
            },
        }

    def _send(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", PRODUCT_PATH, json=payload)

    # ------------------------------------------------------------ 주문
    def fetch_orders(self, since: str) -> list[PurchaseTask]:
        changed = self._request(
            "GET", ORDER_CHANGED_PATH,
            params={"lastChangedFrom": since, "lastChangedType": "PAYED"},
        )
        statuses = (changed.get("data") or {}).get("lastChangeStatuses", []) or []
        order_ids = [s["productOrderId"] for s in statuses if s.get("productOrderId")]
        if not order_ids:
            return []

        detail = self._request(
            "POST", ORDER_QUERY_PATH, json={"productOrderIds": order_ids[:300]}
        )
        tasks: list[PurchaseTask] = []
        for row in (detail.get("data") or []):
            po = row.get("productOrder", {}) or {}
            shipping = po.get("shippingAddress", {}) or {}
            tasks.append(PurchaseTask(
                task_id=f"naver:{po.get('productOrderId')}",
                market=self.market,
                market_order_id=str(po.get("productOrderId", "")),
                product_uid=str(po.get("sellerProductCode") or ""),
                option_value=str(po.get("productOption") or ""),
                quantity=int(po.get("quantity", 1)),
                buyer_paid=int(po.get("totalPaymentAmount", 0)),
                expected_cost=0,
                receiver={
                    "name": shipping.get("name"),
                    "phone": shipping.get("tel1"),
                    "zipcode": shipping.get("zipCode"),
                    "address": shipping.get("baseAddress"),
                    "address_detail": shipping.get("detailedAddress"),
                },
                source_url="",
            ))
        return tasks
