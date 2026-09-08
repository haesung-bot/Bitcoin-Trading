"""쿠팡 Wing OpenAPI 어댑터.

인증: HMAC-SHA256 (CEA 스킴)
    signed-date = UTC 시각을 yymmdd'T'HHMMSS'Z' 로 포맷
    message     = signed-date + HTTP메서드 + 경로(쿼리 제외) + 쿼리스트링(? 제외)
    signature   = HMAC_SHA256(secret_key, message) 의 hex
    Authorization: CEA algorithm=HmacSHA256, access-key=..., signed-date=..., signature=...

쿼리스트링은 요청에 실제로 붙는 것과 '토씨 하나까지' 같아야 서명이 통과한다.
그래서 경로+쿼리를 한 문자열로 만들어 두고 서명과 전송에 같은 값을 쓴다.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from typing import Any

from ..models import Listing, PurchaseTask
from ..utils.logging import get
from .base import MarketAdapter, MarketError

log = get("market.coupang")

PRODUCT_PATH = "/v2/providers/seller_api/apis/api/v1/marketplace/seller-products"
ORDER_PATH = "/v4/vendors/{vendor_id}/ordersheets"


def signed_date(now: float | None = None) -> str:
    dt = datetime.fromtimestamp(now or time.time(), tz=timezone.utc)
    return dt.strftime("%y%m%dT%H%M%SZ")


def build_authorization(method: str, path_with_query: str, access_key: str,
                        secret_key: str, *, now: float | None = None) -> str:
    """CEA Authorization 헤더 값 생성."""
    date = signed_date(now)
    path, _, query = path_with_query.partition("?")
    message = f"{date}{method.upper()}{path}{query}"
    signature = hmac.new(
        secret_key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return (
        f"CEA algorithm=HmacSHA256, access-key={access_key}, "
        f"signed-date={date}, signature={signature}"
    )


class CoupangAdapter(MarketAdapter):
    market = "coupang"
    max_title_len = 100
    max_images = 10

    def required_credentials(self) -> list[str]:
        return ["access_key", "secret_key", "vendor_id", "vendor_user_id"]

    @property
    def base_url(self) -> str:
        return self.cfg.get("base_url", "https://api-gateway.coupang.com").rstrip("/")

    # ------------------------------------------------------------ 요청
    def _request(self, method: str, path_with_query: str,
                 body: dict | None = None) -> dict[str, Any]:
        self.check_credentials()
        auth = build_authorization(
            method, path_with_query,
            self.cfg["access_key"], self.cfg["secret_key"],
        )
        headers = {
            "Authorization": auth,
            "Content-Type": "application/json;charset=UTF-8",
            "X-Requested-By": str(self.cfg["vendor_id"]),
        }
        url = f"{self.base_url}{path_with_query}"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body else None
        resp = self.http.request(method, url, headers=headers, data=data)
        out = resp.json()
        # 쿠팡은 HTTP 200 이어도 code 로 실패를 알린다
        if str(out.get("code")) not in ("200", "SUCCESS", "None", "") and out.get("code"):
            if str(out.get("code")) != "200":
                raise MarketError(f"쿠팡 응답 오류 {out.get('code')}: {out.get('message')}")
        return out

    # ------------------------------------------------------------ 이미지
    def upload_image(self, local_path: str) -> str:
        """쿠팡 이미지 업로드.

        `vendorPath` 에는 쿠팡이 접근 가능한 URL 이 들어가야 한다. 자체 CDN 이
        있으면 그 URL 을 그대로 써도 되고, 없으면 이 엔드포인트로 올려서
        받은 경로를 쓴다. 자체 CDN 을 쓰는 경우 settings 에
        `markets.coupang.cdn_base` 를 지정하면 업로드를 건너뛴다.
        """
        if (cdn := self.cfg.get("cdn_base")):
            return f"{cdn.rstrip('/')}/{local_path.rsplit('/', 1)[-1]}"

        path = "/v2/providers/marketplace_openapi/apis/api/v1/image"
        auth = build_authorization(
            "POST", path, self.cfg["access_key"], self.cfg["secret_key"]
        )
        with open(local_path, "rb") as fh:
            resp = self.http.request(
                "POST", f"{self.base_url}{path}",
                headers={"Authorization": auth,
                         "X-Requested-By": str(self.cfg["vendor_id"])},
                files={"file": (local_path.rsplit("/", 1)[-1], fh, "image/jpeg")},
            )
        data = resp.json()
        url = (data.get("data") or {}).get("cdnPath") or data.get("cdnPath")
        if not url:
            raise MarketError(f"이미지 업로드 응답에 경로가 없습니다: {data}")
        return url

    # ------------------------------------------------------------ 등록
    def build_payload(self, listing: Listing) -> dict[str, Any]:
        vendor_id = self.cfg.get("vendor_id", "")
        items = []
        for opt in (listing.options or [None]):
            value = opt.value if opt else "단일"
            stock = opt.stock if opt else listing.stock
            extra = opt.extra_price if opt else 0
            items.append({
                "itemName": value,
                "originalPrice": listing.sell_price + extra,
                "salePrice": listing.sell_price + extra,
                "maximumBuyCount": max(stock, 0),
                "maximumBuyForPerson": 0,
                "outboundShippingTimeDay": int(self.cfg.get("shipping_days", 3)),
                "unitCount": 1,
                "adultOnly": "EVERYONE",
                "taxType": "TAX",
                "parallelImported": "NOT_PARALLEL_IMPORTED",
                "overseasPurchased": "NOT_OVERSEAS_PURCHASED",
                "pccNeeded": False,
                "externalVendorSku": f"{listing.product_uid}:{value}",
                "images": [
                    {
                        "imageOrder": i,
                        "imageType": "REPRESENTATION" if i == 0 else "DETAIL",
                        "vendorPath": url,
                    }
                    for i, url in enumerate(listing.images)
                ],
                "notices": [
                    {"noticeCategoryName": "신발",
                     "noticeCategoryDetailName": "제조자(수입자)",
                     "content": listing.manufacturer or "상세페이지 참조"},
                    {"noticeCategoryName": "신발",
                     "noticeCategoryDetailName": "제조국",
                     "content": listing.origin or "상세페이지 참조"},
                ],
                "attributes": [
                    {"attributeTypeName": opt.name if opt else "사이즈",
                     "attributeValueName": value}
                ],
                "contents": [{
                    "contentsType": "TEXT",
                    "contentDetails": [
                        {"content": listing.detail_html, "detailType": "TEXT"}
                    ],
                }],
            })

        return {
            "displayCategoryCode": listing.category_code,
            "sellerProductName": listing.title,
            "vendorId": vendor_id,
            "saleStartedAt": _now_iso(),
            "saleEndedAt": "2099-12-31T23:59:59",
            "displayProductName": listing.title,
            "brand": listing.brand,
            "generalProductName": listing.title,
            "productGroup": listing.attributes.get("productGroup", ""),
            "deliveryMethod": "SEQUENCIAL",
            "deliveryCompanyCode": self.cfg.get("delivery_company", "CJGLS"),
            **_delivery_charge_fields(listing),
            "deliveryChargeOnReturn": int(self.cfg.get("return_charge", 5000)),
            "remoteAreaDeliverable": "N",
            "unionDeliveryType": "NOT_UNION_DELIVERY",
            "returnCenterCode": self.cfg.get("return_center_code", ""),
            "returnChargeName": self.cfg.get("return_charge_name", ""),
            "companyContactNumber": self.cfg.get("contact_number", ""),
            "returnZipCode": self.cfg.get("return_zipcode", ""),
            "returnAddress": self.cfg.get("return_address", ""),
            "returnAddressDetail": self.cfg.get("return_address_detail", ""),
            "outboundShippingPlaceCode": self.cfg.get("outbound_place_code", ""),
            "vendorUserId": self.cfg.get("vendor_user_id", ""),
            "requested": False,   # true 면 등록과 동시에 승인 요청
            "items": items,
        }

    def _send(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", PRODUCT_PATH, payload)

    # ------------------------------------------------------------ 주문
    def fetch_orders(self, since: str) -> list[PurchaseTask]:
        vendor_id = self.cfg.get("vendor_id", "")
        path = ORDER_PATH.format(vendor_id=vendor_id)
        query = f"?createdAtFrom={since}&createdAtTo={_today()}&status=ACCEPT"
        data = self._request("GET", f"{path}{query}")

        tasks: list[PurchaseTask] = []
        for sheet in data.get("data", []) or []:
            order_id = str(sheet.get("orderId", ""))
            receiver = sheet.get("receiver", {}) or {}
            for item in sheet.get("orderItems", []) or []:
                sku = str(item.get("externalVendorSkuCode", ""))
                uid, _, option = sku.partition(":")
                tasks.append(PurchaseTask(
                    task_id=f"coupang:{order_id}:{item.get('vendorItemId')}",
                    market=self.market,
                    market_order_id=order_id,
                    product_uid=uid,
                    option_value=option or str(item.get("sellerProductItemName", "")),
                    quantity=int(item.get("shippingCount", 1)),
                    buyer_paid=int(item.get("orderPrice", 0)),
                    expected_cost=0,        # 파이프라인이 DB 에서 채워 넣는다
                    receiver={
                        "name": receiver.get("name"),
                        "phone": receiver.get("safeNumber") or receiver.get("phone"),
                        "zipcode": receiver.get("postCode"),
                        "address": receiver.get("addr1"),
                        "address_detail": receiver.get("addr2"),
                    },
                    source_url="",
                ))
        return tasks


def _delivery_charge_fields(listing: Listing) -> dict[str, Any]:
    """배송비 정책 -> 쿠팡 배송비 필드.

    마진 계산이 전제한 배송비와 등록값이 어긋나면 그대로 손실이 되므로,
    Listing 이 실어 온 정책값을 그대로 옮긴다.
    """
    if listing.shipping_mode == "paid":
        return {
            "deliveryChargeType": "NOT_FREE",
            "deliveryCharge": listing.shipping_charge,
            "freeShipOverAmount": 0,
        }
    if listing.shipping_mode == "conditional":
        return {
            "deliveryChargeType": "CONDITIONAL_FREE",
            "deliveryCharge": listing.shipping_charge,
            "freeShipOverAmount": listing.free_ship_over,
        }
    return {"deliveryChargeType": "FREE", "deliveryCharge": 0, "freeShipOverAmount": 0}


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")
