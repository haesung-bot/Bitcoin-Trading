# Xcoin(XCN) 토큰 배포 가이드

코딩 경험이 없어도 따라 할 수 있게 순서대로 적었습니다.
**반드시 테스트넷에서 먼저 끝까지 해보고**, 그다음에 메인넷으로 가세요.

---

## 0. 어느 체인에 올릴까

| 체인 | 배포 비용 | 전송 수수료(1건) | 추천 상황 |
|---|---|---|---|
| **Polygon PoS** | 약 0.1~0.5 POL | 0.001~0.01 POL (수 원) | **기본 추천.** 게임 보상처럼 소액 전송이 많은 경우 |
| BNB Chain | 약 0.005 BNB | 0.0001~0.0005 BNB (수십 원) | 바이낸스 생태계와 붙일 때 |
| Base | 매우 저렴 | 매우 저렴 | 이더리움 계열 유지하고 싶을 때 |
| Ethereum 메인넷 | 수십만 원 | 건당 수천~수만 원 | **게임 보상에는 부적합** |

이 문서는 **Polygon** 기준으로 씁니다. 다른 체인도 RPC 주소와 체인 ID만 바꾸면 동일합니다.

| | 체인 ID | RPC | 익스플로러 |
|---|---|---|---|
| Polygon 메인넷 | 137 | `https://polygon-rpc.com` | polygonscan.com |
| Polygon Amoy(테스트넷) | 80002 | `https://rpc-amoy.polygon.technology` | amoy.polygonscan.com |

---

## 1. 지갑 두 개를 준비합니다

역할을 나누는 게 중요합니다. 하나가 털려도 전부 잃지 않기 위해서입니다.

- **오너 지갑** — 컨트랙트를 배포하고 소유합니다. 하드웨어 지갑(레저 등)을 강력히 권장.
  이 지갑의 개인키는 **절대 서버에 두지 않습니다.**
- **재무 지갑** — 서버가 유저에게 코인을 보낼 때 쓰는 지갑.
  개인키가 서버 환경변수에 들어가므로, 여기엔 **며칠치 지급분만** 넣어둡니다.

각 지갑에 가스비용 POL을 조금 넣어둡니다. (테스트넷은 [faucet](https://faucet.polygon.technology)에서 무료)

---

## 2. Remix로 배포합니다

1. https://remix.ethereum.org 접속
2. 왼쪽 `File explorer` → `contracts` 폴더에 새 파일 `XCoin.sol` 생성
3. 이 저장소의 `xcoin/contracts/XCoin.sol` 내용을 **전부 복사해서 붙여넣기**
4. 왼쪽 `Solidity compiler` 탭
   - Compiler: `0.8.20` 이상 선택
   - `Advanced Configurations` → **Enable optimization** 체크, runs `200`
   - `Compile XCoin.sol` 클릭 → 초록 체크 확인
5. 왼쪽 `Deploy & run transactions` 탭
   - Environment: **Injected Provider - MetaMask**
   - 메타마스크가 **Polygon 네트워크**에 연결됐는지, **오너 지갑**이 선택됐는지 확인
   - CONTRACT: `XCoin`
   - `Deploy` 옆 입력칸(`INITIALSUPPLY`)에 초기 발행량을 wei 단위로 입력

   초기 발행량 예시 (뒤에 0이 18개 붙습니다):

   | 원하는 수량 | 입력할 값 |
   |---|---|
   | 0개 (필요할 때마다 발행) | `0` |
   | 1,000만 XCN | `10000000000000000000000000` |
   | 1억 XCN | `100000000000000000000000000` |

6. `Deploy` 클릭 → 메타마스크에서 승인
7. 아래 `Deployed Contracts`에 뜬 주소를 **복사해서 잘 보관**합니다. 이게 컨트랙트 주소입니다.

---

## 3. 컨트랙트 소스를 공개(verify)합니다

이걸 해야 유저가 폴리곤스캔에서 코드를 직접 읽어볼 수 있고, 신뢰가 생깁니다.

1. https://polygonscan.com/verifyContract 접속
2. 컨트랙트 주소 입력 → Compiler Type: `Solidity (Single file)` → 버전은 위에서 쓴 것과 동일하게
3. License: MIT
4. 소스코드 붙여넣기, Optimization: **Yes / 200**
5. `Constructor Arguments`는 보통 자동으로 채워집니다. 안 채워지면 배포할 때 넣은 initialSupply를 ABI 인코딩해서 넣습니다.
6. Verify 클릭

---

## 4. 지급 방식을 고릅니다

### 방식 A: transfer (권장)

배포할 때 물량을 한 번에 발행해 오너 지갑에 넣어두고, 그중 일부를 재무 지갑으로 옮겨서 씁니다.

```
오너 지갑에서 → 재무 지갑으로 필요한 만큼 transfer
서버 설정: XCOIN_PAYOUT_MODE=transfer
```

**서버 키가 유출돼도 재무 지갑에 있는 만큼만 잃습니다.** 발행권은 안전합니다.

### 방식 B: mint

재무 지갑을 minter로 지정해서, 전환 요청이 올 때마다 그 자리에서 발행합니다.

```
Remix에서 오너 지갑으로 setMinter(재무지갑주소, true) 실행
서버 설정: XCOIN_PAYOUT_MODE=mint
```

미리 물량을 잡아둘 필요가 없어 편하지만, **서버 키가 털리면 상한까지 마음대로 찍힙니다.**
급하지 않다면 방식 A로 시작하세요.

---

## 5. 서버에 연결합니다

```bash
export XCOIN_RPC_URL="https://polygon-rpc.com"
export XCOIN_CHAIN_ID=137
export XCOIN_CONTRACT_ADDRESS="0x배포된_컨트랙트_주소"
export XCOIN_TREASURY_PRIVATE_KEY="0x재무지갑_개인키"
export XCOIN_PAYOUT_MODE=transfer
```

설정 후 확인:

```bash
curl -H "X-Admin-Secret: $XCOIN_ADMIN_SECRET" http://localhost:8080/admin/chain/status
```

`"mode": "live"` 와 함께 가스 잔고 / 토큰 잔고가 보이면 성공입니다.

---

## 6. 유저 지갑에 토큰이 안 보인다면

메타마스크는 모르는 토큰을 자동으로 표시하지 않습니다.
유저에게 이렇게 안내하세요:

> 메타마스크 → 토큰 → 토큰 가져오기 → 사용자 정의 토큰 →
> 컨트랙트 주소에 `0x...` 붙여넣기 → 심볼 XCN, 소수점 18 자동입력 확인 → 추가

앱에서 자동으로 추가하려면 `wallet_watchAsset`을 쓰면 됩니다.
데모 페이지(`/demo`)에 그 코드가 들어 있습니다.

---

## 7. 운영 체크리스트

- [ ] 재무 지갑 가스 잔고 알림을 걸어둔다 (`/admin/chain/status`를 주기적으로 확인)
- [ ] 재무 지갑엔 며칠치 지급분만 유지한다
- [ ] 오너 지갑 개인키는 하드웨어 지갑 / 오프라인 보관
- [ ] `XCOIN_ADMIN_SECRET`, `XCOIN_SERVER_SECRET`은 충분히 길게, 깃허브에 올리지 않는다
- [ ] 서버 DB(`xcoin.db`)를 정기적으로 백업한다 — 여기가 날아가면 유저 포인트가 사라진다
- [ ] 처음엔 `XCOIN_AUTO_APPROVE=false`로 두고 전환 요청을 눈으로 보면서 시작한다

---

## 8. 법적 유의사항 (한국 기준)

게임 포인트를 실제로 거래 가능한 코인으로 바꿔주는 구조는 규제 대상이 될 수 있습니다.
서비스를 공개하기 전에 반드시 확인하세요.

- **게임산업법**: 게임물 이용 결과로 얻은 것을 환전하거나 환전 알선하는 행위는 금지될 수 있습니다.
  이 때문에 국내 P2E 게임은 게임물관리위원회 등급분류를 받지 못한 사례가 많습니다.
  실제로 국내 서비스를 계획한다면 등급분류 가능 여부를 먼저 확인해야 합니다.
- **가상자산이용자보호법**: 토큰 발행·유통 주체로서의 의무가 생길 수 있습니다.
- **세무**: 유저에게 지급한 코인이 소득으로 잡힐 수 있습니다.

기술적으로 동작하는 것과 합법적으로 서비스할 수 있는 것은 별개입니다.
게임/블록체인 쪽을 다뤄본 변호사에게 한 번 상담받고 가는 편이 훨씬 쌉니다.
