# CLAUDE.md

이 문서는 이 저장소에서 작업하는 AI 어시스턴트(Claude 등)를 위한 안내서입니다.
코드베이스 구조, 개발 워크플로우, 핵심 규칙을 정리합니다.

> 저장소 언어는 한국어입니다. 로그 메시지, UI 텍스트, 주석, 커밋 메시지 모두 한국어를 기본으로
> 작성합니다. 새 코드를 추가할 때도 기존 스타일(한국어 주석/메시지)을 따르세요.

## 프로젝트 개요

5개 거래소(Gate.io / Binance / Bybit / OKX / Bitget)를 지원하는 **BTC 무기한 선물 자동매매**
시스템입니다. **SuperTrend** 추세 신호 + **ATR 트레일링 스탑**을 결합한 전략을 15분봉 고정으로
운용합니다. 배포는 Windows용 `.exe`로 이루어지며, 서버 기반 **코드 라이선스**로 사용을 통제하고,
**텔레그램 봇**으로 신규 유저에게 프로그램과 라이선스 코드를 자동 배포합니다.

세 개의 독립 실행 단위로 구성됩니다:

| 컴포넌트 | 파일 | 실행 위치 | 역할 |
|---|---|---|---|
| **트레이딩 봇 (클라이언트)** | `gateio_supertrend_bot.py` | 사용자 PC (Windows exe) | Tkinter GUI + ccxt 매매 엔진 |
| **라이선스 서버** | `license_code_server.py` | 운영자 서버 (Render) | Flask + SQLite 코드 발급/검증/차단 |
| **텔레그램 배포 봇** | `telegram_welcome_bot.py` | 운영자 서버 (Render) | 신규 유저 온보딩 + 코드 자동 발급 |
| 라이선스 클라이언트 모듈 | `license_client_code.py` | exe 안에 포함 | 봇 시작 시 서버에 코드 검증 요청 |

## 아키텍처 및 데이터 흐름

```
[텔레그램 그룹] 신규 유저 유입
      │ telegram_welcome_bot.py: 환영 → 버튼 → 개인방 /start
      ▼
[텔레그램 봇] ──POST /admin/generate (X-Admin-Secret)──▶ [라이선스 서버]
      │                                                        │ SQLite: codes 테이블에 코드 생성
      │ 다운로드 링크 + 활성화 코드 전달                        │
      ▼                                                        │
[사용자] exe 실행 ──POST /api/verify {code, device_id}──▶ [라이선스 서버]
      │                                                        │ 코드 검증 + 기기 바인딩 → 서명 토큰 발급
      │ 토큰 캐시(~/.bot_license_code_cache.json)               │
      ▼                                                        │
[트레이딩 봇] ──POST /api/check_token (1시간마다 재검증)──▶ [라이선스 서버]
      │                                                        │ DB 최신 상태로 차단/만료/기기 확인
      ▼
[ccxt] 거래소 API ◀──▶ SuperTrend + ATR 트레일링 스탑 매매 루프
```

### 라이선스 검증 3-상태 모델 (중요)

`license_client_code.py`는 서버 응답을 반드시 세 가지로 구분합니다. 이 구분을 깨뜨리면
정상 유저의 프로그램이 오작동으로 종료되므로 절대 단순화하지 마세요:

- **`valid`**: 서버가 명확히 유효하다고 응답 → 통과
- **`invalid`**: 서버가 명확히 차단/만료/기기불일치라고 응답 → 프로그램 종료(`os._exit(1)`)
- **`unreachable`**: 네트워크 실패/타임아웃 (Render 무료 플랜 콜드스타트 등) → **종료하지 않고** 재시도

핵심 원칙: **네트워크 문제를 "차단"으로 오인하면 안 된다.** `unreachable`일 때 프로그램을
끄면 서버가 잠깐 잠든 사이 모든 유저가 튕깁니다.

### 토큰 갱신 로직 (중요)

토큰은 24시간(`TOKEN_VALID_SECONDS`) 유효 서명 토큰입니다. 하지만 시간 만료가 곧 차단은
아닙니다. `parse_token()`은 **서명만** 검증하고 시간 만료는 실패로 보지 않습니다. 대신
`/api/check_token`이 DB 최신 상태로 실제 차단/만료를 판단하고, 시간만 지났으면 **새 토큰을
재발급**해 돌려줍니다. 클라이언트는 응답의 새 토큰으로 캐시를 갱신합니다. 이 흐름 덕분에
무기한 코드가 24시간 뒤 "만료"로 오인돼 꺼지지 않습니다.

## 트레이딩 엔진 세부 (`gateio_supertrend_bot.py`)

- **진입 클래스**: `GateioProSuperTrendBot`, `if __name__ == "__main__"`에서
  `ensure_license_active()` 통과 후에만 GUI 실행, `start_periodic_recheck()`로 주기 재검증 시작.
- **거래소 추상화**: `EXCHANGE_OPTIONS` 딕셔너리가 거래소별 ccxt 클래스 후보/옵션/passphrase
  필요 여부를 정의. `_get_exchange_class()`가 ccxt 버전차(예: `gate`/`gateio`)를 흡수.
  OKX·Bitget은 `needs_passphrase=True`(ccxt 통합 필드명은 `password`).
- **고정 전략 파라미터** (화면 비노출, 상수로 하드코딩):
  `FIXED_TIMEFRAME="15m"`, `FIXED_ST_PERIOD=10`, `FIXED_ST_MULTIPLIER=3`,
  `FIXED_ATR_PERIOD=22`, `FIXED_ATR_MULTIPLIER=3`, `FIXED_POLL_SEC=10`.
  전략값을 바꾸려면 이 상수를 직접 수정.
- **지표 계산은 외부 라이브러리 없이 직접 구현**: `compute_supertrend_engine()`(SuperTrend),
  `compute_atr_series()`(Wilder ATR). pandas만 사용, `ta`/`pandas_ta` 등 미사용.
- **신호는 "마감된 캔들" 기준**: `get_closed_candle_signals()`에서 `idx=-2`(마지막 완성봉)
  사용. 진행 중인 봉으로 매매하지 않음.
- **매매 루프** `trading_loop()` (별도 daemon 스레드): ① 보유 중이면 ATR 트레일링 스탑
  이탈 우선 청산 → ② 트레일링 미도달 상태에서 추세 전환 시 스위칭(폴백) 청산 →
  ③ **추세가 실제로 전환된 시점(`trend_changed`)에만** 신규 진입. 트레일링 청산만으로는
  같은 방향 재진입하지 않음.
- **재시작 복원**: `reconstruct_trailing_extreme()`가 거래소에 남은 포지션의 생성 시각
  이후 캔들을 조회해 진입 후 실제 최고가/최저가를 복원 → 재시작 전 트레일링 상태를 이어감.
- **주문 금액 2가지 모드**: `fixed`(고정 USDT) / `balance_pct`(잔고 비율 복리, 진입마다
  실시간 잔고 재조회). 복리 모드는 `BALANCE_PCT_SAFETY_MARGIN=5.0`(%p)을 빼서 수수료 여유 확보.
- **스레드 안전**: 백그라운드 매매 스레드에서 Tkinter 위젯을 직접 만지지 않음. 로그는
  `self.log()` → `root.after(0, ...)`로 메인 스레드에 위임. 레버리지 등은 루프 시작 시
  `self._current_leverage`로 한 번만 읽어 저장.

### 로컬 상태 파일 (사용자 홈 디렉토리)

- `~/.gateio_supertrend_bot_config.json`: 거래소별 API 키 (평문, `chmod 600`). 거래소명 키로
  중첩. 구버전 단일 형식은 로드 시 Gate.io 전용으로 자동 마이그레이션.
- `~/.gateio_supertrend_bot_trades.json`: 매매 기록. `TRADE_RECORD_KEEP_KEYS`만 유지(불필요
  필드는 로드 시 정리).
- `~/.bot_license_code_cache.json`: 라이선스 토큰/코드 캐시.

## 라이선스 서버 세부 (`license_code_server.py`)

- **DB**: SQLite. `codes`(코드/상태/기기바인딩/만료) + `trial_users`(텔레그램 체험 중복방지).
  `init_db()`가 구버전 스키마에 `bound_device_id`/`expires_at` 컬럼을 마이그레이션.
- **DB 경로 우선순위**: `DATA_DIR` 환경변수 → `/data`(Render 영구 디스크) → 코드 폴더(로컬).
  **Render 배포 시 영구 디스크 미설정이면 재배포마다 코드/유저 기록이 초기화됨** — 경고 로그 출력.
- **인증**:
  - 사용자 API(`/api/verify`, `/api/check_token`): 코드 + 기기ID 기반. 코드는 첫 사용
    기기에 자동 바인딩, 다른 기기면 거부(중복사용 방지).
  - 관리자 API(`/admin/*`): `X-Admin-Secret` 헤더 == `ADMIN_SECRET` 환경변수.
  - 토큰 서명: `SERVER_SECRET`으로 HMAC-SHA256.
- **주요 엔드포인트**: `/api/verify`, `/api/check_token`, `/health`,
  `/admin/generate`(코드발급, `duration_days`/`duration_minutes`/무기한),
  `/admin/block`·`/admin/unblock`(차단/해제), `/admin/reset_device`(기기 초기화),
  `/admin/mark_checked`, `/admin/list`, 텔레그램용 `/admin/check_trial_user`·
  `/admin/mark_trial_issued`·`/admin/list_trial_users`·`/admin/reset_trial_user`.
- **운영 모델**: 거래소 어필리에이트 대시보드에서 실거래를 육안 확인 → 장기 미거래 코드를
  수동 `/admin/block`. 자동 거래소 API 연동은 없음.

## 텔레그램 봇 세부 (`telegram_welcome_bot.py`)

- `python-telegram-bot` v20+ (async) + 헬스체크용 Flask (`/`, Render port).
- 흐름: 그룹 신규멤버 환영 → 인라인 버튼 → 개인방 `/start` → 라이선스 서버에서 코드 발급
  → 다운로드 링크 + 코드 DM → 운영자에게 알림.
- `TRIAL_DURATION_DAYS=None`(무기한)/숫자(기간제) 토글. 중복 발급은 라이선스 서버의
  `trial_users`로 방지.
- `faq.json` 키워드 매칭 무인 FAQ 응답. 미매칭 시 운영자 프로필 링크 버튼 안내.

## 개발 워크플로우

### 실행

```bash
# 트레이딩 봇 (GUI, 데스크톱 — Tkinter 필요)
pip install ccxt pandas requests
python gateio_supertrend_bot.py     # 실행 시 라이선스 코드 입력창이 먼저 뜸

# 라이선스 서버 (로컬 테스트)
pip install flask --break-system-packages
python license_code_server.py       # 기본 포트 5000, PORT 환경변수로 변경

# 텔레그램 봇
pip install python-telegram-bot flask requests
python telegram_welcome_bot.py      # BOT_TOKEN 환경변수 필요
```

> `requirements.txt`는 **서버(Render) 배포용**이라 `Flask`만 담고 있습니다 (UTF-16 인코딩).
> 트레이딩 봇은 별도로 `ccxt pandas requests`가 필요합니다.

### 테스트

- 자동화된 테스트 스위트(pytest 등)는 **없습니다**. 변경 후에는 해당 컴포넌트를 직접 실행해
  동작을 확인하세요.
- 라이선스 서버는 `/health`로 살아있는지, `duration_minutes`로 짧은 만료 코드를 만들어
  만료/재검증 흐름을 빠르게 검증할 수 있습니다.
- `fix.py`는 특정 유저의 체험 중복/기기 바인딩을 SQLite에서 직접 푸는 **일회성 운영
  스크립트**입니다. 일반 개발에는 사용하지 마세요.

### exe 빌드 (Windows 전용)

빌드는 **반드시 Windows PC**에서 PyInstaller로 수행합니다 (크로스 컴파일 불가).
상세 절차와 배포 체크리스트는 `exe_빌드_방법.md`를 참고. `.spec` 파일
(`멀티거래소선물pro자동매매.spec`)이 `license_client_code.py`를 `--add-data`로 포함합니다.

```bash
pyinstaller --onefile --noconsole --name "GateSuperTrendBot" \
  --add-data "license_client_code.py;." gateio_supertrend_bot.py
```

### Git 규칙

- 커밋 메시지는 한국어 위주(기존 이력 참고). 명확하고 서술적으로.
- **PR은 사용자가 명시적으로 요청할 때만** 생성.
- 지정된 개발 브랜치에서 작업하고 해당 브랜치로만 푸시.

## 배포 전 보안 체크리스트 (반드시 확인)

이 저장소는 **공개 배포용 코드**이므로 비밀값 취급에 주의하세요.

- [ ] `license_code_server.py`의 `SERVER_SECRET`/`ADMIN_SECRET`은 환경변수로만 주입하고,
      기본 placeholder(`CHANGE_THIS_...`)를 실제 배포에 그대로 두지 말 것.
- [ ] `telegram_welcome_bot.py`에는 현재 `BOT_TOKEN`/`ADMIN_SECRET`의 실제 값이
      하드코딩된 기본값으로 남아 있음 → **새 비밀값은 반드시 환경변수로만** 넣고 코드에
      평문으로 커밋하지 말 것. 노출된 값은 로테이션 권장.
- [ ] `license_client_code.py`의 `LICENSE_SERVER_URL`이 실제 운영 서버 주소인지 확인.
- [ ] 사용자에게는 exe만 배포. 서버 코드/시크릿은 절대 배포물에 포함하지 말 것.
- [ ] 커밋에 새로운 API 키/토큰/시크릿을 절대 추가하지 말 것.

## 저장소 내 참고 문서 (한국어)

- `exe_빌드_방법.md` / `EXE파일 만드는방법.txt`: exe 빌드 절차
- `멀티거래소선물pro자동매매/API키_발급방법_안내.md`: 거래소별 API 키 발급 안내
- `깃허브에 zip파일 업로드방법.txt`, `텔레그램 수정후 cmd입력.txt`: 운영 메모
- `배포용문구.txt`, `배포용사진.png`: 마케팅 자료
- `faq.json`: 텔레그램 봇 FAQ 데이터

## 커밋하지 않는(또는 주의할) 산출물

`build/`, `__pycache__/`, `테스트/`, `*.zip`, `*.exe`, `license_codes.db`(로컬 DB),
배포용 바이너리/이미지 등은 빌드/배포 산출물입니다. 소스 변경과 섞지 말고, 새로 커밋하기
전에 이런 대용량/생성 파일이 포함되지 않았는지 확인하세요.

## 핵심 규칙 요약 (AI 어시스턴트용)

1. **언어**: 모든 사용자 대면 텍스트·주석·로그는 한국어.
2. **라이선스 3-상태 모델**을 절대 단순화하지 말 것 (`unreachable` ≠ `invalid`).
3. **시간 만료 ≠ 차단**: 토큰 24시간 만료는 재발급 대상일 뿐, 종료 사유가 아님.
4. **매매 신호는 마감된 봉(`idx=-2`)** 기준. 진행 중 봉으로 진입 금지.
5. **스레드 안전**: 매매 스레드에서 Tkinter 위젯 직접 접근 금지, `root.after`로 위임.
6. **비밀값은 환경변수로**. 새 시크릿을 코드에 하드코딩/커밋하지 말 것.
7. 전략 파라미터 변경은 `FIXED_*` 상수에서.
8. 테스트 스위트가 없으므로 변경 후 **직접 실행 검증**.
