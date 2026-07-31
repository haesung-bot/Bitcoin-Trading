# 테스트넷 시작하기

가짜 돈으로 진짜와 똑같이 굴려보는 단계입니다. **비용은 0원**이고, 실수해도 잃을 게 없습니다.
여기서 끝까지 돌려본 다음에 메인넷으로 가세요.

전체 소요 시간: **20~30분** (수도꼭지 기다리는 시간 포함)

---

## 준비물

- 파이썬 3.9 이상
- 메타마스크 (크롬 확장 또는 모바일 앱)
- 인터넷

```bash
pip install -r xcoin/requirements.txt
```

---

## 1단계 · 지갑 두 개 만들기 (2분)

역할을 나눕니다. 하나가 털려도 전부 잃지 않기 위해서입니다.

- **오너 지갑** — 컨트랙트를 배포하고 소유합니다
- **재무 지갑** — 서버가 유저에게 코인을 보낼 때 씁니다 (개인키가 서버에 들어갑니다)

```bash
python xcoin/contracts/new_wallet.py
```

주소 두 개, 개인키 두 개, 복구구문 두 개가 나옵니다. **메모장에 붙여두세요.**

> ⚠️ 이 스크립트로 만든 지갑은 **테스트넷 전용**입니다.
> 개인키가 화면에 그대로 찍히므로 터미널 기록에 남습니다.
> 메인넷 지갑은 반드시 메타마스크나 하드웨어 지갑에서 만드세요.

---

## 2단계 · 메타마스크에 Amoy 테스트넷 추가 (3분)

**네트워크 추가**

메타마스크 → 좌측 상단 네트워크 → `네트워크 추가` → `네트워크 수동 추가`

| 항목 | 값 |
|---|---|
| 네트워크 이름 | `Polygon Amoy` |
| 새 RPC URL | `https://rpc-amoy.polygon.technology` |
| 체인 ID | `80002` |
| 통화 기호 | `POL` |
| 블록 탐색기 URL | `https://amoy.polygonscan.com` |

**지갑 불러오기**

메타마스크 → 계정 아이콘 → `계정 추가 또는 하드웨어 지갑` → `개인 키 가져오기`
→ 1단계에서 만든 **오너 지갑 개인키** 붙여넣기. 재무 지갑도 같은 방식으로 추가.

---

## 3단계 · 무료 테스트 POL 받기 (5분)

가스비로 쓸 돈입니다. 실제 가치는 없습니다.

https://faucet.polygon.technology

1. Network: **Amoy** 선택
2. Token: **POL** 선택
3. 주소 칸에 **오너 지갑 주소** 붙여넣기
4. `Submit` → 1~3분 뒤 도착

**재무 지갑에도 똑같이 받아두세요.** 유저에게 코인 보낼 때 가스로 씁니다.

> 수도꼭지가 하루 한도에 걸리면 아래 대안을 쓰세요.
> - https://www.alchemy.com/faucets/polygon-amoy
> - https://faucets.chain.link/polygon-amoy

받은 뒤 메타마스크에서 잔고가 보이면 성공입니다. 각각 **0.1 POL 이상**이면 충분합니다.

**실제로 얼마나 드는가** (측정값 기준)

| 작업 | 가스 | Amoy 기준 비용 |
|---|---|---|
| 컨트랙트 배포 | 1,131,333 | 약 0.034 POL |
| 유저에게 1회 지급 | 53,592 | 약 0.0016 POL |

0.1 POL이면 배포하고도 **50건 넘게** 지급할 수 있습니다.

---

## 4단계 · 리허설 (1분, 가스 안 듦)

진짜로 쏘기 전에 가상 체인에서 똑같은 과정을 그대로 돌려봅니다.

```bash
pip install "web3[tester]"
python xcoin/contracts/deploy.py --dry-run
```

```
▸ 컨트랙트 배포
   완료 (가스 1,131,333)
▸ 배포 결과 확인
   ✓ 이름: Xcoin
   ✓ 심볼: XCN
   ...
  리허설 완료. 전 과정이 문제없이 돌았습니다.
```

여기서 실패하면 설치 문제입니다. 실제 배포로 넘어가지 마세요.

---

## 5단계 · 테스트넷에 배포 (3분)

```bash
export XCOIN_DEPLOYER_KEY="0x오너지갑_개인키"

python xcoin/contracts/deploy.py --network amoy \
  --initial-supply 10000000 \
  --treasury 0x재무지갑_주소 \
  --treasury-fund 1000000
```

이 명령이 하는 일:
1. Xcoin(XCN)을 Amoy에 배포하고
2. 1,000만 개를 오너 지갑에 발행하고
3. 그중 100만 개를 재무 지갑으로 옮깁니다

> `export`를 쓰기 싫으면 그냥 `python xcoin/contracts/deploy.py --network amoy` 로 실행하세요.
> 개인키를 물어봅니다. 입력해도 화면에 표시되지 않습니다.

성공하면 이렇게 끝납니다:

```
  배포 완료
  익스플로러에서 보기:
    https://amoy.polygonscan.com/address/0x....

  서버에 넣을 환경변수 (그대로 복사하세요):
    export XCOIN_RPC_URL="https://rpc-amoy.polygon.technology"
    export XCOIN_CHAIN_ID=80002
    export XCOIN_CONTRACT_ADDRESS="0x..."
    export XCOIN_PAYOUT_MODE=transfer
```

**저 링크를 눌러 폴리곤스캔에서 직접 확인해보세요.** 여러분 토큰이 실제로 올라가 있습니다.

배포 기록은 `xcoin/contracts/deployments/amoy.json`에도 저장됩니다.

---

## 6단계 · 설정 점검 (1분)

서버를 켜기 전에, 뭐가 빠졌는지 먼저 확인합니다.

```bash
export XCOIN_RPC_URL="https://rpc-amoy.polygon.technology"
export XCOIN_CHAIN_ID=80002
export XCOIN_CONTRACT_ADDRESS="0x배포된_주소"
export XCOIN_TREASURY_PRIVATE_KEY="0x재무지갑_개인키"
export XCOIN_PAYOUT_MODE=transfer

python xcoin/contracts/check_live.py --network amoy
```

전부 `✓`면 통과입니다:

```
[4] 재무 지갑 (서버가 지급에 쓰는 지갑)
  ✓ 가스 잔고  0.100000 POL
  ✓ 지급 방식  transfer
  ✓ 토큰 잔고  1,000,000 XCN

[5] 지급 여력
  ✓ 가스로 처리 가능한 건수  약 62 건

  전부 정상입니다. 서버를 켜도 됩니다.
```

`✖`가 뜨면 무엇을 어떻게 고치라고 알려줍니다. 고친 뒤 다시 돌리세요.

---

## 7단계 · 서버 켜고 전체 흐름 돌려보기 (5분)

```bash
export XCOIN_ADMIN_SECRET="아무거나-긴-문자열"
export XCOIN_SERVER_SECRET="또다른-긴-문자열"
# 6단계의 체인 환경변수도 그대로 유지

python xcoin/run_server.py
```

시작 로그에 `'mode': 'live'`가 보이면 진짜 체인에 연결된 것입니다.
(`simulation`이면 환경변수가 안 먹은 것입니다.)

**게임 등록**

새 터미널에서:

```bash
curl -X POST http://localhost:8080/admin/games \
  -H "X-Admin-Secret: $XCOIN_ADMIN_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"game_id":"puzzle_01","name":"블록 퍼즐","points_per_score":0.5,"daily_point_cap":3000}'
```

응답의 `secret`을 복사해 두세요.

**데모에서 처음부터 끝까지**

브라우저에서 http://localhost:8080/demo

1. 게임 ID `puzzle_01`, secret 붙여넣기, 유저 ID 아무거나 → **접속하기**
2. 슬라이드 퍼즐 풀기 → **결과 제출** → 포인트가 쌓입니다
3. 몇 판 더 해서 1000포인트 이상 모으기
4. **메타마스크로 연동하기** → 서명 창이 뜨면 승인
   (자산은 이동하지 않습니다. 소유 확인용 서명입니다.)
5. **지갑에 Xcoin 표시하기** → 메타마스크에 XCN이 추가됩니다
6. 전환할 포인트 입력 → **코인으로 바꾸기**
7. 30초 안에 지급 워커가 처리합니다. 전환 내역에 `sent`와 tx 해시가 뜹니다

**메타마스크를 열어보세요. XCN이 실제로 들어와 있습니다.**

---

## 8단계 · 블록체인에서 직접 확인

전환 내역에 뜬 tx 해시를 복사해서:

```
https://amoy.polygonscan.com/tx/여기에_붙여넣기
```

`Transfer` 이벤트에 재무 지갑 → 유저 지갑으로 XCN이 이동한 기록이 보입니다.
이게 블록체인에 영구히 남는 증거입니다.

토큰 전체 현황은:

```
https://amoy.polygonscan.com/token/컨트랙트_주소
```

---

## 문제가 생겼을 때

### `RPC 에 연결할 수 없습니다`

공용 RPC가 가끔 불안정합니다. 다른 주소로 시도하세요.

```bash
python xcoin/contracts/deploy.py --network amoy \
  --rpc https://polygon-amoy-bor-rpc.publicnode.com
```

### `가스가 없습니다`

수도꼭지에서 POL을 아직 못 받았거나, 다른 주소로 받았습니다.
메타마스크에서 **Amoy 네트워크로 전환한 뒤** 잔고를 확인하세요.

### 서버가 `simulation` 모드로 뜬다

`XCOIN_RPC_URL`, `XCOIN_CONTRACT_ADDRESS`, `XCOIN_TREASURY_PRIVATE_KEY`
**세 개가 모두** 있어야 live로 뜹니다. 하나라도 비면 시뮬레이션입니다.

```bash
env | grep XCOIN_
```

### 전환은 됐는데 상태가 `approved`에서 안 넘어간다

지급 워커가 30초마다 돕니다. 기다리거나 직접 실행하세요.

```bash
curl -X POST http://localhost:8080/admin/payouts/run \
  -H "X-Admin-Secret: $XCOIN_ADMIN_SECRET"
```

### 상태가 `failed`가 됐다

```bash
curl -H "X-Admin-Secret: $XCOIN_ADMIN_SECRET" \
  "http://localhost:8080/admin/conversions?status=failed"
```

`error` 필드에 이유가 있습니다. 대부분 가스 부족입니다.
**포인트는 자동으로 환불되어 있으니** 가스를 채우고 다시 전환하면 됩니다.

### 메타마스크에 XCN이 안 보인다

메타마스크는 모르는 토큰을 자동으로 표시하지 않습니다.
데모의 **지갑에 Xcoin 표시하기** 버튼을 누르거나, 수동으로:

메타마스크 → 토큰 → `토큰 가져오기` → `사용자 정의 토큰`
→ 컨트랙트 주소 붙여넣기 → 심볼과 소수점은 자동으로 채워집니다

### 서명 창이 안 뜬다

브라우저에 메타마스크가 설치돼 있어야 합니다.
모바일이라면 메타마스크 앱 **안의 브라우저**로 데모 주소를 열어야 합니다.

---

## 테스트넷에서 꼭 해봐야 할 것

메인넷 가기 전에 이것들을 확인하세요.

- [ ] 퍼즐 여러 판 → 포인트 정상 적립
- [ ] 일일 한도까지 채워보기 → 한도에서 멈추는지
- [ ] 지갑 연동 → 서명 거부했을 때 에러 처리
- [ ] 전환 → 메타마스크에 실제 도착
- [ ] 폴리곤스캔에서 tx 확인
- [ ] 잔액보다 많이 전환 시도 → 거부되는지
- [ ] 같은 판 두 번 제출 → 중복 지급 안 되는지
- [ ] 재무 지갑 가스를 일부러 비우고 전환 → `failed` 후 포인트 환불되는지
- [ ] `XCOIN_AUTO_APPROVE=false`로 두고 수동 승인 흐름 확인
- [ ] 서버를 껐다 켜도 포인트가 남아 있는지 (영구 디스크 확인)
- [ ] 게임 2~3개 등록해서 합산 포인트가 맞는지

마지막 항목이 특히 중요합니다. `XCOIN_DATA_DIR`을 설정하지 않으면
재배포할 때마다 유저 포인트가 전부 사라집니다.

---

## 메인넷으로 갈 때 달라지는 것

| | 테스트넷 (Amoy) | 메인넷 (Polygon) |
|---|---|---|
| `--network` | `amoy` | `polygon` |
| 체인 ID | 80002 | 137 |
| 가스 | 무료 (수도꼭지) | 실제 POL 구매 필요 |
| 오너 지갑 | 스크립트로 생성 | **하드웨어 지갑 필수** |
| 실수 | 다시 하면 됨 | **되돌릴 수 없음** |
| 배포 전 확인 | 없음 | `배포` 입력 확인 절차 |

메인넷 배포 명령:

```bash
python xcoin/contracts/deploy.py --network polygon \
  --initial-supply 10000000 \
  --treasury 0x재무지갑_주소 \
  --treasury-fund 100000
```

메인넷은 `배포`라고 직접 입력해야 진행됩니다. 실수 방지 장치입니다.

메인넷으로 넘어가기 전 [`contracts/DEPLOY.md`](contracts/DEPLOY.md)의
운영 체크리스트와 **법적 유의사항**을 반드시 읽어보세요.
한국에서 게임 포인트를 코인으로 환전해주는 구조는 게임산업법상 규제 대상이 될 수 있습니다.
