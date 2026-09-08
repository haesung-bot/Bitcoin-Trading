# autocommerce — 상품 수집 → 마진/SEO → 이미지 변환 → 오픈마켓 등록 자동화

```
[1. 크롤러]  ABC마트 / 브랜드 공식몰 / 픽스처
      │  RawProduct (명칭, 원가, 옵션, 이미지 URL)
      ▼
[2. 마진 계산 + SEO 키워드 엔진]
      │  PricedProduct (최종 판매가 + 손익 내역서, 상품명/태그)
      ▼
[3. 이미지 파이프라인]  로컬 규격화 / Replicate FLUX
      │  MediaSet (1000×1000 흰배경 JPEG + 계보 기록)
      ▼
[4. 오픈마켓 어댑터] ──► 네이버 스마트스토어 / 쿠팡 Wing
      │  ListingResult
      ▼
[5. 주문 → 1-Click 확인 구매]  ← 결제 승인만 사람이
```

## 30초 만에 돌려보기

```bash
cd commerce_automation
pip install -r requirements.txt

python3 -m autocommerce doctor                              # 설정·의존성 점검
python3 -m autocommerce price --cost 89000                  # 마진 시뮬레이션
python3 -m autocommerce run --source fixture --markets naver,coupang
python3 -m autocommerce listings --limit 1                  # 생성된 등록 페이로드
python3 -m autocommerce status
```

`config/settings.yaml` 이 없으면 `settings.example.yaml` 로 자동 실행되고,
기본이 **dry-run** 이라 마켓에 아무것도 전송하지 않습니다.

### 실행 결과 예시

```
$ python3 -m autocommerce price --cost 62000 --markets naver,coupang

정책 'standard' | 목표 18% (sell_price 기준) | VAT general | 유료배송
원가 62,000 + 매입배송 0 + 택배 3,000 + 부자재 500 = 65,500원
  naver 수수료 5.74%  (commission 3.74%, sales_link 2.00%)
  coupang 수수료 11.88%  (commission 11.88%)

마켓          상품가     배송비     총매출     수수료     VAT     순이익   마진율
------------------------------------------------------------------------------
naver        85,900     3,000     88,900     5,103   1,663    16,634   18.7%
coupang      92,900     3,000     95,900    11,393   1,728    17,279   18.0%
```

---

## 1. 크롤러

| 소스 | 상태 | 비고 |
|---|---|---|
| `fixture` | 바로 사용 | `data/fixtures/*.json` — 개발·테스트·데모용 |
| `abcmart` | 셀렉터 설정 필요 | JSON-LD 우선 파싱, 실패 시 CSS 셀렉터 폴백 |

* **robots.txt 를 기본으로 준수합니다** (`crawler.respect_robots: true`).
  끄는 옵션이 있지만 끄지 마세요 — 차단·법적 리스크는 운영자 책임입니다.
* 도메인당 요청 속도는 토큰 버킷으로 제한합니다 (기본 0.5 req/s = 2초에 1회).
* 상품 1건의 파싱 실패가 배치를 죽이지 않습니다. 실패는 `CrawlReport` 에 모입니다.
* 가격 0원·이미지 없음 같은 오파싱은 `validate()` 에서 잘라냅니다.

**새 소스 추가**: `BaseCrawler` 를 상속해 `discover()` 와 `parse()` 두 개만 구현하고
`crawlers/registry.py` 에 등록하면 됩니다.

> ABC마트/공식몰 크롤러는 **틀만 제공**합니다. 실제 운영 전에 대상 사이트의
> robots.txt 와 이용약관에서 수집 허용 범위를 확인하고, `selectors` 를 실제 DOM 에
> 맞게 채우세요. 사이트가 개편되면 코드가 아니라 `settings.yaml` 만 고치면 됩니다.

## 2. 마진 계산

목표 순마진에서 판매가를 **역산**합니다. 계산식은
[`pricing/margin.py`](autocommerce/pricing/margin.py) 상단 docstring에 전부 적혀 있습니다.

```
일반과세 · 판매가 기준:   R = T / (1 − r − 1.1·m)      P = R − Sc
    R  = 총 매출 = 상품가 P + 고객 청구 배송비 Sc
    T  = 원가 + 매입배송 + 택배 단가 + 부자재
    r  = 판매수수료 + 결제수수료 + 매출연동 + 광고 유보
    m  = 목표 순마진율        1.1 = 부가세(10/11) 보정
```

**왜 상품가가 아니라 총 매출 기준인가.** 네이버·쿠팡 모두 배송비를 포함한
결제금액 전체에 수수료를 매깁니다. 유료배송 3,000원을 따로 받으면 그 3,000원에도
수수료가 붙습니다. 상품가 기준으로 계산하면 그만큼이 통째로 새므로, 계산은
총 매출로 하고 마지막에 `P = R − Sc` 로 되돌립니다. 무료배송이면 `Sc = 0` 이라
식이 그대로 성립합니다.

* `vat_mode`: `general`(일반과세) / `simplified`(간이과세) / `none`
* `margin_basis`: `sell_price`(총 매출 기준) / `cost`(원가 마크업)
* `shipping_mode`: `free`(무료배송) / `paid`(유료) / `conditional`(N원 이상 무료)
  — 마진 계산이 전제한 배송비가 마켓 등록 페이로드까지 그대로 실려 갑니다.
  둘이 어긋나면 그 차액이 곧바로 손실이 되기 때문입니다.
* `rounding`: `charm`(끝자리 900) / `nearest` / `none` — **반올림이 목표 마진 아래로
  내려가지 않도록 항상 올림**하며, 끝자리는 고객에게 보이는 **상품가**에 겁니다.
* 역산 후 **정방향으로 다시 계산**해 손익 내역서(`PriceBreakdown`)를 남깁니다.
  자동화에서 가장 비싼 실수는 값이 틀린 줄 모르고 대량 등록되는 것이라,
  판매가가 왜 그 숫자인지 전부 저장해 둡니다.

### 수수료율의 VAT 처리 — 가장 틀리기 쉬운 지점

마켓마다 수수료 고지 방식이 다릅니다. 네이버 주문관리 수수료(3.74%)는 **VAT 포함**,
쿠팡 판매수수료(10.8%)는 통상 **VAT 별도**입니다. 이걸 그냥 더하면 쿠팡 비용이
과소 계상됩니다. 그래서 항목별로 명시할 수 있습니다.

```yaml
fees:
  naver:
    commission: 0.0374                                # 숫자만 = VAT 포함
    sales_link: 0.02
  coupang:
    commission: {rate: 0.108, vat_included: false}    # → 실효 11.88%
```

`price` 명령이 실효 수수료율과 항목별 내역을 같이 찍어 주니 값을 바꾼 뒤 꼭 확인하세요.

**가드**(`pricing.policies.*.guards`) — 하나라도 걸리면 등록을 **보류**합니다.

| 가드 | 잡아내는 사고 |
|---|---|
| `min_net_profit` | 배송비·수수료에 먹혀 남는 게 없는 상품 |
| `max_price_multiple` | 원가를 1/10 로 잘못 파싱 → 판매가 폭등 |
| `min_price` / `max_price` | 마켓 정책 범위 이탈 |

### 현재 확정된 설정값

| 항목 | 값 | 근거 |
|---|---|---|
| 부가세 | `general` (일반과세) | 사업자 미등록 상태 — 등록 후 간이과세면 `vat_mode: simplified` 한 줄만 변경 |
| 네이버 수수료 | **5.74%** = 3.74%(네이버페이) + 2%(매출연동) | 광고 미집행 |
| 쿠팡 수수료 | **11.88%** = 10.8% × 1.1 | 마켓플레이스(판매자배송) 기준, VAT 별도 고지분 |
| 배송비 | 유료 3,000원 별도 | 상품가가 3,000원 낮게 노출돼 가격비교에서 유리 |
| 목표 순마진 | 18% (총 매출 기준) | |

> 쿠팡을 **로켓그로스**로 전환하면 입출고요금·배송비·보관료가 별도로 붙어 실효
> 수수료가 17~20%대까지 올라갑니다. `fees.coupang` 에 항목을 추가하세요.

## 3. SEO 키워드 엔진

상품명은 **더하기보다 거르기**가 먼저입니다. 네이버·쿠팡 모두 홍보 문구,
중복 단어, 특수문자 남발을 어뷰징으로 보고 노출을 깎습니다.

```
{브랜드} {모델명} {상품유형} {핵심속성}
  → 정규화(전각/이모지/[프로모션] 제거)
  → 금칙어 제거     ("최저가", "정품", "무료배송" …)
  → 사이즈 토큰 제거 (한국 신발 규격 200~330mm 5단위만)
  → 중복 제거 → 길이 컷(기본 100자)
```

잘라낸 단어는 `dropped_terms` 에 남겨 왜 빠졌는지 추적할 수 있습니다.

> **`에어맥스 90` 의 `90` 은 남고 `270` 은 빠집니다.** 단순 숫자 제거는
> 모델명(`993`, `530`, `97`)을 날려버립니다.

## 4. 이미지 파이프라인

```
원본 → [Replicate FLUX 변환(선택, 대표이미지만)] → 규격화 → 마켓 업로드
```

* **규격화**(로컬, Pillow): 1000×1000 흰 배경 정사각, 비율 유지, EXIF 제거, sRGB, JPEG.
* **생성형 변환**(선택, `provider: replicate`): 비용 때문에 대표이미지에만 적용합니다.
  변환에 실패하면 원본으로 진행하고 경고만 남깁니다.
* Pillow 가 없으면 원본을 통과시키고 경고합니다 — 이미지 한 단계 때문에
  파이프라인 전체가 멈추지 않게.
* 모든 산출물에 **계보(provenance)**(원본 URL, 변환 종류, 시각)를 기록합니다.

> **저작권 가드.** 상품 이미지는 브랜드·판매처의 저작물입니다.
> `imaging.licensing.require_source_license: true`(기본값)이면
> `licensed_sources` 에 등록된 소스만 처리하고 나머지는 건너뜁니다.
> 사용 권한을 확보한 소스만 여기에 추가하세요.

## 5. 오픈마켓 어댑터

| 마켓 | 인증 | 등록 |
|---|---|---|
| 네이버 스마트스토어 | OAuth2 + bcrypt 전자서명 | `POST /external/v2/products` |
| 쿠팡 Wing | HMAC-SHA256 (CEA) | `POST .../marketplace/seller-products` |

* 파이프라인은 마켓 스키마를 모릅니다. 중립 표현 `Listing` 만 만들고 어댑터가 번역합니다.
* **`preflight()`** 가 전송 전에 상품명 길이·이미지 수·가격·카테고리·재고를 검사합니다.
  API 400 을 맞기 전에 여기서 걸립니다.
* **dry-run 이 기본값**입니다. 페이로드를 만들어 DB 에 저장하되 전송하지 않습니다.
  `listings` 명령으로 검수한 뒤 `--live` 로 전환하세요.
* dry-run 은 '등록됨'으로 기록하지 않으므로 다음 실행에서 다시 처리됩니다.

**11번가·G마켓 추가**: `MarketAdapter` 를 상속해 `build_payload()`,
`_send()`, `fetch_orders()`, `required_credentials()` 를 구현하면 됩니다.

## 6. 1-Click 확인 구매

> **결제는 자동화하지 않습니다.** 3D Secure, 간편결제 인증, CAPTCHA, 1인 구매 제한이
> 얽혀 있고, 뚫으면 (a) 약관·전자금융거래법 위반 소지 (b) 카드사 이상거래 탐지로 카드 정지
> (c) 가격이 오른 줄 모르고 역마진 주문이 대량 발생합니다.

```
[자동]  주문 수집 → 원가 재확인 → 배송지 정규화 → 구매 링크/입력값 준비
─────────────────── 사람 1회 확인 ───────────────────
[수동]  결제 승인 (열린 페이지에서 직접 클릭)
[자동]  주문번호 기록
```

```bash
python3 -m autocommerce orders sync --markets naver,coupang --since 2026-09-01T00:00:00
python3 -m autocommerce orders list
```

```
┌─ 확인 구매 #naver:12345
│ 마켓/주문     naver / 12345
│ 상품          fixture:A1
│ 옵션 / 수량   270 / 1개
│ 고객 결제액   99,000원
│ 예상 매입가   62,000원
│ 예상 차익     37,000원
│ 배송지        홍길동 / 010-0000-0000
│               (06236) 서울시 강남구 101동 101호
│ 구매 링크     https://shop.test/p?prdtNo=A1&size=270
└─ 결제는 위 링크에서 직접 진행하세요. 시스템은 결제하지 않습니다.
```

```bash
python3 -m autocommerce orders approve --task-id naver:12345 --order-no ORD-99
python3 -m autocommerce orders reject  --task-id naver:12345 --reason "공홈 품절"
```

매입가가 `cost_drift_block`(기본 3,000원) 이상 오르면 자동 승인 후보에서도 빠집니다.
`auto_approve` 는 기본 `false` 이며, 켜도 역마진 주문은 차단됩니다.

---

## 상태 관리 / 멱등성

SQLite(`var/autocommerce.db`) 한 파일이 세 가지를 책임집니다.

1. **멱등성** — `uid`(소스:상품코드) + `content_hash`(가격·재고·이미지) 로 판정.
   변한 게 없으면 건너뛰고, 가격이나 재고가 바뀌면 다시 처리합니다.
2. **재개** — 중간에 죽어도 마지막으로 도달한 단계부터 다시 시작합니다.
3. **감사** — 어떤 페이로드를 보냈고 뭐가 돌아왔는지 전부 남깁니다.

상품 수가 수십만 건을 넘으면 같은 스키마로 Postgres 에 옮기면 됩니다.

## 설정과 비밀값

```bash
cp config/settings.example.yaml config/settings.yaml
cp .env.example .env         # 실제 키는 여기에만 (.gitignore 처리됨)
```

`settings.yaml` 에는 `${ENV_NAME}` 참조만 씁니다. 값 자체를 적지 마세요 —
`tests/test_config.py` 가 예시 파일에 리터럴 비밀값이 들어가면 실패시킵니다.

## 의존성

| 패키지 | 필수 | 없으면 |
|---|---|---|
| `requests`, `PyYAML` | ✅ | 실행 불가 |
| `Pillow` | ⭕ | 이미지 규격화 생략 + 경고 |
| `bcrypt` | ⭕ | 네이버 인증 불가 (dry-run 은 정상) |

## 테스트

```bash
python3 -m unittest discover -s tests -t .     # 100 tests
```

## 실 운영 전 체크리스트

- [ ] 대상 사이트 robots.txt·이용약관 확인, 이미지 사용 권한 확보
- [ ] `crawler.sources.abcmart.selectors` 를 실제 DOM 에 맞게 작성
- [ ] `categories.map` 에 취급 카테고리 전부 등록 (미매핑은 경고만 나고 기본값으로 등록됨)
- [ ] `pricing.policies.*.fees` 를 **실제 정산 내역서** 기준으로 재확인
      (현재 값은 공개 요율 기준 추정치입니다 — 카테고리·계약별로 다릅니다)
- [ ] 사업자 등록 후 `vat_mode` 확정 (일반/간이)
- [ ] 계약 택배 단가가 3,000원이 아니면 `shipping_cost` 수정
- [ ] `.env` 자격증명 채우고 `doctor` 통과
- [ ] dry-run 으로 `listings` 페이로드 눈으로 검수
- [ ] 소량(`--limit 3`)으로 `--live` 시험 등록 → 마켓 관리자에서 확인
- [ ] 그 다음에 물량 확대
