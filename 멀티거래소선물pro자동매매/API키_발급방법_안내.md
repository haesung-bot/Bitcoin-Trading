# 거래소별 API Key 발급 방법 안내

이 프로그램을 사용하려면 이용하시는 거래소에서 API Key를 발급받아야 합니다.
아래에서 본인이 쓰시는 거래소를 찾아 순서대로 따라 하시면 됩니다.

⚠️ **공통 주의사항**
- API Key는 **선물(Futures/Perpetual) 거래 권한**을 반드시 체크해야 합니다.
- **출금(Withdraw) 권한은 절대 체크하지 마세요.** 프로그램은 매매만 하면 되고, 출금 권한까지 주면 키 유출 시 자금을 통째로 잃을 위험이 있습니다.
- 발급 즉시 **API Key, Secret Key를 메모장에 복사해서 저장**하세요. Secret Key는 발급 화면을 벗어나면 다시 볼 수 없고, 잃어버리면 키를 처음부터 새로 만들어야 합니다.
- 이 프로그램은 **API Key**로 거래소에 연결합니다. API Key는 로그인 비밀번호가 아니니 혼동하지 마세요.

---

## 1. Gate.io

1. Gate.io 로그인 후 우측 상단 프로필 아이콘 클릭 → **API Management(API 관리)** 이동
2. **Create New Key(새 키 생성)** 클릭
3. API Key Type은 **API v4 Key** 선택
4. Permissions(권한)에서 **Perpetual Futures(무기한 선물)** 체크 + **Read And Write** 권한 부여
5. (선택) IP 화이트리스트 등록 — 보안 강화하고 싶으면 사용
6. 자금 비밀번호 입력 + 2FA(구글 인증 등) 인증
7. API Key, Secret Key 발급 완료 → 즉시 메모장에 저장

⚠️ **자주 발생하는 문제**: 선물 지갑에 USDT를 한 번도 이체한 적이 없으면, 권한을 다 체크했어도 `USER_NOT_FOUND` 오류가 날 수 있습니다. **현물(Spot) → 선물(Futures) 지갑으로 USDT를 소액이라도 먼저 이체**해두면 해결됩니다. (지갑 → 이체 메뉴에서 가능)

---

## 2. Binance

1. Binance 로그인 후 우측 상단 프로필 아이콘 → **API Management** 이동
2. API 이름을 입력하고 **Create** 클릭
3. 이메일 인증 및 2FA(OTP) 인증 완료
4. API Key, Secret Key가 발급됨 (Secret Key는 이 화면에서만 표시되니 바로 복사)
5. 발급된 키 옆의 **Edit restrictions** 클릭 → **Enable Futures** 체크

⚠️ **자주 발생하는 문제**: 선물 계좌를 먼저 개설(활성화)해두지 않으면 "Enable Futures" 항목 자체가 안 보이거나 선택이 안 됩니다. Binance 앱/웹에서 **선물(Futures) 탭에 먼저 들어가서 계좌 개설** 절차를 완료한 뒤 키를 발급하세요.

---

## 3. Bybit

1. Bybit 로그인 후 프로필 아이콘 클릭 → **API** 메뉴 이동
2. **API Management** → **Create New Key** → **System-generated API Keys** 선택
3. API 용도 선택 및 이름 입력
4. 권한에서 **Contract(파생상품/Derivatives) - Orders & Positions** 체크
5. (선택) IP 제한 설정
6. 2FA 인증 후 생성 → API Key, Secret Key 즉시 저장

⚠️ **참고**: Bybit는 통합계좌(UTA) 방식이라, 선물 지갑이 아니라 **통합 계좌에 자금이 있어야** 정상적으로 거래/잔고 조회가 됩니다. 자금이 현물 지갑에만 있다면 통합계좌로 이체해두세요.

---

## 4. OKX ⚠️ Passphrase 필요

OKX는 API Key, Secret Key 외에 **Passphrase(비밀번호)까지 총 3개**가 필요합니다. 잊지 말고 같이 저장하세요.

1. OKX 로그인 후 프로필 아이콘 → **API** 또는 **API Key** 메뉴 이동
2. **Create API Key** 클릭
3. API 이름 입력, **Passphrase 설정** (API Key/Secret Key와는 완전히 별개의 값입니다 — 직접 정하는 비밀번호)
4. Permissions(권한)에서 **Trade** 체크
5. 2FA 인증 후 생성
6. API Key, Secret Key, **Passphrase** 3개 모두 메모장에 저장

⚠️ 이 프로그램 화면에서 OKX를 선택하면 Passphrase 입력칸이 자동으로 나타납니다. 꼭 같이 입력해주세요.

---

## 5. Bitget ⚠️ Passphrase 필요

Bitget도 OKX처럼 **Passphrase까지 총 3개**가 필요합니다.

1. Bitget 로그인 후 프로필 아이콘 → **API Management** 이동
2. **Create New API** 클릭
3. Notes(이름) 입력, **Passphrase 설정** (영문/숫자 조합 8자 이상, 특수문자 사용 불가)
4. 권한에서 **Read-write** + **Futures(선물) - Orders & Holdings** 체크
5. 2FA 인증 후 생성
6. API Key, Secret Key, **Passphrase** 3개 모두 메모장에 저장

⚠️ Passphrase를 잊어버리면 복구할 방법이 없고, API Key를 처음부터 다시 만들어야 합니다.

---

## 발급 후 프로그램에 입력하는 방법

1. 프로그램 실행 → 상단 **"거래소"** 드롭다운에서 본인이 발급받은 거래소 선택
2. **API Key**, **Secret Key** 입력 (OKX/Bitget은 **Passphrase**도 함께 입력)
3. "API 키 저장" 체크 시 다음 실행부터 자동으로 입력되어 있음 (본인 PC에서만 사용 권장)
4. 레버리지, 주문 금액 등 나머지 설정 후 매매 시작

---

## 공통 문제 해결

**Q. 연동 시도했는데 오류가 난다**
A. 아래 순서로 확인해보세요.
1. API Key/Secret Key를 복사할 때 앞뒤 공백이 같이 복사되지 않았는지 확인
2. 선물(Futures/Perpetual) 거래 권한이 체크되어 있는지 확인
3. OKX/Bitget이면 Passphrase까지 입력했는지 확인
4. 선물 지갑(또는 통합계좌)에 최소한의 자금이 있는지 확인

**Q. API Key를 잘못 만들었다 / 다시 만들고 싶다**
A. 거래소 API 관리 페이지에서 기존 키를 삭제(Delete)하고 위 과정을 다시 진행하면 됩니다. 삭제해도 계좌 자금에는 영향 없습니다.

**Q. 2FA(구글 인증) 앱이 없는데 어떻게 하나요**
A. Google Authenticator, Authy 등 무료 앱을 스마트폰에 설치하고, 거래소 보안 설정에서 2FA를 먼저 등록해야 API Key 발급이 가능합니다.
