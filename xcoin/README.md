# Xcoin — 퍼즐게임 포인트 → 코인 전환 시스템

모바일 퍼즐게임에서 얻은 **포인트**를 **Xcoin(XCN)** 이라는 실제 코인으로 바꿔주는 전체 시스템입니다.
게임을 몇 개 만들었든 서버 하나에 다 붙일 수 있습니다.

```
 [퍼즐게임 앱]        [게임 백엔드]         [Xcoin 서버]          [블록체인]
      │                   │                    │                    │
  한 판 종료 ──점수──▶ secret으로 서명 ──▶ /points/submit            │
      │                   │             치트검사 · 한도 · 포인트 적립  │
      │                                        │                    │
  지갑 연결 ◀──── 서명 문구 ──── /wallet/nonce ─┤                    │
      │──── 서명 ──────────▶ /wallet/link ─────▶ 소유 확인 후 저장     │
      │                                        │                    │
  "코인으로 바꾸기" ───────▶ /exchange/convert ─▶ 포인트 차감 · 대기열  │
                                               │                    │
                                         지급 워커 ──── transfer ───▶ 유저 지갑
```

---

## 지금 바로 돌려보기 (5분)

```bash
pip install -r xcoin/requirements.txt

export XCOIN_ADMIN_SECRET="아무거나-긴-문자열"
export XCOIN_SERVER_SECRET="또다른-긴-문자열"

python xcoin/run_server.py
```

블록체인 설정이 없으면 **시뮬레이션 모드**로 뜹니다.
포인트 적립, 지갑 연동, 전환까지 전부 진짜처럼 동작하고 실제 전송만 가짜 해시로 기록됩니다.

### 1) 게임 등록

```bash
curl -X POST http://localhost:8080/admin/games \
  -H "X-Admin-Secret: $XCOIN_ADMIN_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"game_id":"puzzle_01","name":"블록 퍼즐","points_per_score":0.1,"daily_point_cap":2000}'
```

응답에 나오는 `secret`을 복사해 두세요. **이때 한 번만 보여줍니다.**

### 2) 데모 페이지 열기

브라우저에서 http://localhost:8080/demo

게임 ID와 secret, 유저 ID를 넣고 접속 → 슬라이드 퍼즐을 풀고 → 결과 제출 →
포인트 확인 → 메타마스크로 지갑 연동 → 코인으로 전환.

전체 흐름이 한 화면에서 돕니다.

### 3) 테스트

```bash
python xcoin/tests/test_xcoin.py
```

32개 테스트가 돕니다. 대부분 "이상한 요청을 제대로 막는지" 확인하는 것들입니다.

---

## 폴더 구조

```
xcoin/
├── server/
│   ├── config.py      설정 (전부 환경변수로 덮어쓰기 가능)
│   ├── db.py          SQLite 스키마 · 트랜잭션
│   ├── security.py    HMAC 서명 · 유저 토큰 · 관리자 인증
│   ├── points.py      포인트 적립/차감 · 치트 검사 · 일일 한도
│   ├── wallet.py      지갑 소유 증명 (EIP-191 서명)
│   ├── exchange.py    포인트 → 코인 전환 · 지급 워커 · 환불
│   ├── chain.py       실제 온체인 전송 (web3) / 시뮬레이션
│   ├── admin.py       운영자 API
│   └── app.py         HTTP 라우트
├── contracts/
│   ├── XCoin.sol         ERC-20 토큰 (외부 의존성 없는 단일 파일)
│   ├── XCoin.build.json  컴파일 결과 (abi + 바이트코드, 바로 배포 가능)
│   ├── deploy.py         배포 스크립트 (--dry-run 으로 리허설 가능)
│   ├── check_live.py     배포/설정 점검
│   ├── new_wallet.py     테스트넷용 지갑 생성
│   ├── test/             컨트랙트 EVM 테스트
│   └── DEPLOY.md         배포 가이드 (Remix 수동 배포용)
├── sdk/
│   ├── xcoin.js       게임 백엔드(Node) + 웹 클라이언트 SDK
│   └── XcoinClient.cs 유니티용 클라이언트
├── demo/index.html    실제 동작하는 데모 (퍼즐 + 지갑 + 전환)
├── tests/             통합 테스트
└── run_server.py      실행 진입점
```

---

## 내 게임에 붙이기

### 왜 게임 백엔드가 필요한가

점수를 앱이 직접 서버로 보내면, 앱을 뜯어서 "점수 999999" 요청을 만드는 건 어렵지 않습니다.
그래서 **게임 secret은 여러분 서버에만** 두고, 서버가 서명해서 Xcoin 서버로 보냅니다.

> 백엔드가 아예 없는 게임이라면? 아래 "백엔드 없이 붙이기"를 보세요.

### Node.js 백엔드 예시

```js
const { XcoinBackend } = require('./xcoin/sdk/xcoin.js');

const xcoin = new XcoinBackend({
  serverUrl: process.env.XCOIN_SERVER_URL,
  gameId: 'puzzle_01',
  secret: process.env.XCOIN_GAME_SECRET,   // 절대 앱에 넣지 않기
});

// 유저가 로그인했을 때 — 앱에 내려줄 토큰 발급
app.post('/xcoin/token', async (req, res) => {
  const userId = req.session.userId;             // 여러분 인증에서 가져온 값
  res.json(await xcoin.issueUserToken(userId));
});

// 한 판이 끝났을 때
app.post('/xcoin/score', async (req, res) => {
  const userId = req.session.userId;
  const { score, duration_ms, nonce } = req.body;
  res.json(await xcoin.submitScore(userId, score, duration_ms, nonce));
});
```

### 유니티 게임 쪽

```csharp
var xcoin = gameObject.AddComponent<Xcoin.XcoinClient>();
xcoin.serverUrl  = "https://xcoin.내도메인.com";
xcoin.backendUrl = "https://게임백엔드.내도메인.com";

// 로그인 직후
StartCoroutine(xcoin.Login(myUserId));

// 한 판 끝났을 때
StartCoroutine(xcoin.SubmitScore(score, durationMs,
    res => Debug.Log($"{res.awarded} 포인트 적립")));

// 포인트 화면
StartCoroutine(xcoin.GetBalance(b => pointLabel.text = $"{b.points} P"));

// 지갑 연동 (웹 페이지를 열어서 처리)
xcoin.OpenWalletLinkPage();
```

### 백엔드 없이 붙이기

정 백엔드를 만들 수 없다면 차선책이 있습니다. **완벽하지는 않다는 걸 알고 쓰세요.**

1. `XCOIN_AUTO_APPROVE=false`로 두고 **모든 전환을 사람이 승인**합니다.
2. `points_per_score`를 낮게, `daily_point_cap`을 빡빡하게 잡습니다.
3. `/admin/sessions?rejected=1`로 이상한 제출을 주기적으로 봅니다.
4. 게임 secret은 앱 안에 들어가므로 **유출을 전제**하고, 유출이 확인되면
   `/admin/games/<id>/rotate-secret`으로 갈아끼웁니다.

포인트가 실제 돈이 되는 순간 사람들은 반드시 뚫으려 합니다.
백엔드 하나 만드는 게 훨씬 쌉니다.

---

## 게임을 여러 개 붙일 때

게임마다 `game_id`와 `secret`을 따로 발급하면 됩니다. 포인트 지갑은 유저 단위로 공유됩니다.

```bash
# 게임 3개 등록
for g in puzzle_01 puzzle_02 puzzle_03; do
  curl -X POST http://localhost:8080/admin/games \
    -H "X-Admin-Secret: $XCOIN_ADMIN_SECRET" -H "Content-Type: application/json" \
    -d "{\"game_id\":\"$g\",\"name\":\"$g\",\"points_per_score\":0.1,\"daily_point_cap\":2000}"
done
```

게임마다 난이도가 다르니 `points_per_score`(점수 1점당 포인트)를 조절해서
"어느 게임을 해도 시간당 비슷한 포인트"가 되게 맞추는 게 좋습니다.

- 한 게임에서만 하루 최대: `daily_point_cap` (게임별)
- 모든 게임 합쳐서 하루 최대: `XCOIN_DAILY_POINT_CAP` (유저별)

유저가 세 게임을 다 해도 하루 총량은 두 번째 값으로 막힙니다.

---

## API 목록

### 게임 백엔드용

| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/auth/session` | 유저 토큰 발급 (게임 서명 필요) |
| POST | `/points/submit` | 게임 결과 제출 (게임 서명 필요) |

### 앱용 (X-User-Token 헤더 필요)

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/config` | 코인 정보와 정책 (토큰 불필요) |
| GET | `/points/balance` | 포인트 잔액 |
| GET | `/points/history` | 포인트 내역 |
| GET | `/wallet/nonce` | 지갑 서명용 문구 발급 |
| POST | `/wallet/link` | 지갑 연동 |
| GET | `/wallet` | 연동 상태 |
| POST | `/wallet/unlink` | 연동 해제 |
| GET | `/exchange/quote` | 전환 견적 (토큰 불필요) |
| POST | `/exchange/convert` | 포인트 → 코인 전환 요청 |
| GET | `/exchange/conversions` | 전환 내역 |

### 운영자용 (X-Admin-Secret 헤더 필요)

| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/admin/games` | 게임 등록 (secret 발급) |
| GET | `/admin/games` | 게임 목록 |
| POST | `/admin/games/<id>/update` | 지급률·한도·활성화 변경 |
| POST | `/admin/games/<id>/rotate-secret` | secret 재발급 |
| GET | `/admin/stats` | 전체 현황 |
| GET | `/admin/chain/status` | 재무 지갑 가스·토큰 잔고 |
| GET | `/admin/conversions` | 전환 요청 목록 |
| POST | `/admin/conversions/<id>/approve` | 승인 |
| POST | `/admin/conversions/<id>/reject` | 거절 (포인트 환불) |
| POST | `/admin/payouts/run` | 지급 즉시 실행 |
| POST | `/admin/payouts/recover` | 멈춘 건 복구 |
| GET | `/admin/users/<id>` | 유저 상세 |
| POST | `/admin/users/<id>/block` | 유저 차단 |
| POST | `/admin/points/adjust` | 포인트 수동 지급/회수 |
| GET | `/admin/sessions` | 플레이 기록 (치트 조사) |

---

## 부정 사용을 막는 장치들

| 공격 | 막는 방법 |
|---|---|
| 가짜 점수 요청 | 게임 secret HMAC 서명 검증 |
| 같은 판 여러 번 제출 | `nonce` 유니크 제약 (DB 레벨) |
| 오래된 요청 재전송 | 타임스탬프 ±5분 |
| 말도 안 되는 점수 | 세션당 최대 점수 |
| 봇/매크로 | 최소 플레이 시간, 초당 점수 상한, 분당 제출 횟수 |
| 하루 종일 돌리기 | 게임별 + 유저별 일일 포인트 한도 |
| 남의 잔액 조회 | 유저 토큰 (HMAC, 24시간 만료) |
| 지갑 도용 | EIP-191 서명으로 소유 증명 |
| 계정 여러 개로 한 지갑 | 지갑 주소 유니크 제약 |
| 네트워크 재시도로 이중 전환 | `idempotency_key` 유니크 제약 |
| 이중 지급 | `approved → sending` 원자적 선점 |

전환 실패 시에는 자동으로 포인트가 환불되고, 환불도 중복되지 않습니다.
(`refund:<전환ID>`라는 고정 키를 쓰기 때문입니다.)

---

## 실제 코인으로 전환하기

시뮬레이션으로 충분히 돌려본 뒤에 진행하세요.
**먼저 테스트넷입니다** — [`TESTNET.md`](TESTNET.md)를 따라가면 20분이면 끝나고 비용은 0원입니다.

```bash
python xcoin/contracts/new_wallet.py                      # 지갑 만들기
python xcoin/contracts/deploy.py --dry-run                # 리허설 (가스 안 듦)
python xcoin/contracts/deploy.py --network amoy \
    --treasury 0x재무지갑 --treasury-fund 1000000          # 테스트넷 배포
python xcoin/contracts/check_live.py --network amoy       # 설정 점검
```

메인넷으로 갈 때는:

1. `contracts/DEPLOY.md`를 따라 Xcoin(XCN) 토큰을 배포합니다 (Polygon 권장)
2. 환경변수를 채웁니다:

```bash
export XCOIN_RPC_URL="https://polygon-rpc.com"
export XCOIN_CHAIN_ID=137
export XCOIN_CONTRACT_ADDRESS="0x배포된_주소"
export XCOIN_TREASURY_PRIVATE_KEY="0x재무지갑_개인키"
export XCOIN_PAYOUT_MODE=transfer
```

3. 확인:

```bash
curl -H "X-Admin-Secret: $XCOIN_ADMIN_SECRET" http://localhost:8080/admin/chain/status
```

`"mode": "live"`가 뜨면 이제 진짜로 나갑니다.

---

## 설정값 전체

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `XCOIN_ADMIN_SECRET` | (변경 필요) | 관리자 API 열쇠 |
| `XCOIN_SERVER_SECRET` | (변경 필요) | 유저 토큰 서명 키 |
| `XCOIN_DATA_DIR` | – | DB를 둘 영구 디스크 경로 |
| `XCOIN_POINTS_PER_COIN` | 1000 | 1 XCN이 되는 포인트 |
| `XCOIN_MIN_CONVERT_POINTS` | 1000 | 최소 전환 포인트 |
| `XCOIN_CONVERT_FEE_BPS` | 0 | 전환 수수료 (100 = 1%) |
| `XCOIN_DAILY_POINT_CAP` | 5000 | 유저별 하루 적립 한도 |
| `XCOIN_DAILY_CONVERT_CAP` | 50000 | 유저별 하루 전환 한도 |
| `XCOIN_AUTO_APPROVE` | true | 전환 자동 승인 여부 |
| `XCOIN_DAY_UTC_OFFSET` | 9 | 일일 한도 초기화 기준 시간대 (9=KST) |
| `XCOIN_MAX_SCORE_PER_SESSION` | 100000 | 한 판 최대 점수 |
| `XCOIN_MIN_SESSION_SECONDS` | 5 | 한 판 최소 플레이 시간 |
| `XCOIN_MAX_SCORE_PER_SECOND` | 500 | 초당 점수 상한 |
| `XCOIN_MAX_SUBMITS_PER_MINUTE` | 10 | 분당 제출 횟수 |
| `XCOIN_REQUIRE_USER_TOKEN` | true | 유저 API에 토큰 요구 |
| `XCOIN_RPC_URL` | – | 체인 RPC (없으면 시뮬레이션) |
| `XCOIN_CHAIN_ID` | 137 | 체인 ID (137=Polygon) |
| `XCOIN_CONTRACT_ADDRESS` | – | 배포한 토큰 주소 |
| `XCOIN_TREASURY_PRIVATE_KEY` | – | 지급 지갑 개인키 |
| `XCOIN_PAYOUT_MODE` | transfer | `transfer` 또는 `mint` |
| `XCOIN_PAYOUT_INTERVAL` | 30 | 지급 워커 주기(초) |
| `XCOIN_PAYOUT_MAX_RETRY` | 5 | 전송 실패 재시도 횟수 |
| `PORT` | 8080 | 서버 포트 |

---

## 배포

### Render / Railway

- Start command: `python xcoin/run_server.py`
- Persistent Disk를 붙이고 `XCOIN_DATA_DIR`을 마운트 경로와 똑같이 설정
  (이걸 빼먹으면 **재배포할 때마다 유저 포인트가 전부 사라집니다**)
- 환경변수에 secret들을 넣습니다

### 운영 전 체크리스트

- [ ] `XCOIN_ADMIN_SECRET` / `XCOIN_SERVER_SECRET` 을 길고 무작위한 값으로
- [ ] 영구 디스크 연결 확인 (서버 로그에 경고가 안 떠야 함)
- [ ] `xcoin.db` 정기 백업 — 여기가 날아가면 유저 포인트가 사라집니다
- [ ] HTTPS 필수 (유저 토큰과 관리자 secret이 헤더로 오갑니다)
- [ ] CORS를 게임 도메인으로 좁히기 (`app.py`의 `_cors`)
- [ ] 처음 한 달은 `XCOIN_AUTO_APPROVE=false`로 두고 눈으로 확인
- [ ] 재무 지갑에는 며칠치 지급분만
- [ ] 가스 잔고 모니터링 (`/admin/chain/status`)

---

## 알아두어야 할 한계

솔직하게 적어둡니다.

1. **SQLite 기준입니다.** 동시 접속 수천 명까지는 무난하지만, 그 이상이면
   PostgreSQL로 옮겨야 합니다. `db.py`만 바꾸면 되도록 짜뒀습니다.
2. **지급 워커는 프로세스 안에서 돕니다.** 서버를 여러 대로 늘리면
   워커는 한 대에서만 돌게 하세요(`XCOIN_PAYOUT_INTERVAL`을 아주 크게 주고
   `/admin/payouts/run`을 크론으로 부르는 방법도 있습니다).
3. **모바일 앱 안에서의 지갑 연동**은 웹 페이지를 여는 방식입니다.
   앱 안에서 바로 하려면 WalletConnect를 붙여야 하는데 설정이 꽤 복잡합니다.
4. **컨트랙트 감사를 받지 않았습니다.** 실제 돈이 걸리기 전에
   테스트넷에서 충분히 굴려보고, 규모가 커지면 감사를 받으세요.

---

## ⚠️ 법적 유의사항 (중요)

**게임 포인트를 실거래 가능한 코인으로 바꿔주는 구조는 한국에서 규제 이슈가 있습니다.**

- **게임산업법**: 게임 이용 결과로 얻은 것을 환전하거나 환전을 알선하는 행위가 제한됩니다.
  이 때문에 국내에서 P2E 게임이 등급분류를 받지 못한 사례가 여러 번 있었습니다.
- **가상자산이용자보호법**: 토큰 발행·유통 주체로서의 의무가 생길 수 있습니다.
- **세무**: 유저에게 지급한 코인이 소득으로 잡힐 수 있습니다.

기술적으로 동작하는 것과 합법적으로 서비스하는 것은 다른 문제입니다.
국내 출시를 계획한다면 **게임물관리위원회 등급분류 가능 여부를 먼저 확인**하고,
게임·블록체인 분야 변호사에게 상담을 받고 시작하시길 권합니다.
해외 출시라면 그 나라 규제를 확인해야 합니다.

이 코드는 그런 판단이 끝난 뒤에 쓰라고 만든 도구입니다.

---

## 상세 문서

- **테스트넷 시작 (여기부터)**: [`TESTNET.md`](TESTNET.md)
- 토큰 배포 (Remix 수동): [`contracts/DEPLOY.md`](contracts/DEPLOY.md)
- 컨트랙트 코드: [`contracts/XCoin.sol`](contracts/XCoin.sol)
