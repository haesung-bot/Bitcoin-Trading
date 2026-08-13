# 개인용 exe 만드는 방법 (martingale_bot_mine.py)

운영자 본인이 쓰는 개인용 프로그램을 exe로 만드는 방법입니다.
배포용(`HedgedMartingaleBot.exe`)과 **이름·설정·매매상태가 모두 분리되어 있어서**
한 PC에서 두 개를 같이 켜 둬도 서로 간섭하지 않습니다.

⚠️ exe 빌드는 반드시 **Windows PC**에서 해야 합니다. PyInstaller는 빌드하는 컴퓨터의
운영체제용 실행파일만 만들 수 있어서, Linux/Mac에서 만든 파일은 Windows에서 실행되지 않습니다.

---

## 1단계: 폴더 준비

새 폴더(예: 바탕화면에 `개인용봇`)를 만들고 아래 **2개 파일**을 그 안에 넣습니다.

- `hedged_martingale_bot.py`   (매매 엔진 — 배포용과 같은 파일)
- `martingale_bot_mine.py`     (개인용 화면)

⚠️ 두 파일은 **반드시 같은 폴더**에 있어야 합니다 (화면 파일이 엔진 파일을 불러옵니다).

💡 배포용과 **같은 폴더에 넣어도 됩니다.** 엔진 파일은 하나만 있으면 되고,
화면 파일 2개(`hedged_martingale_bot_gui.py`, `martingale_bot_mine.py`)를 각각
빌드하면 exe 2개가 나옵니다.

---

## 2단계: 필요한 프로그램 설치

cmd(명령 프롬프트)를 열고 순서대로 입력합니다.

```
cd 개인용봇_폴더경로
pip install pyinstaller ccxt requests
```

(tkinter는 Windows용 파이썬에 기본 포함이라 따로 설치할 필요 없습니다.)

---

## 3단계: exe 빌드

같은 cmd 창에서:

```
python -m PyInstaller --onefile --noconsole --name "MartingaleBotMine" --collect-all ccxt --collect-all certifi martingale_bot_mine.py
```

- `--onefile` : 파일 하나로 묶기
- `--noconsole` : 실행 시 검은 콘솔창 안 뜨게 하기 (화면만 뜸)
- `--name "MartingaleBotMine"` : 만들어질 exe 이름. **배포용과 다른 이름을 써야**
  두 exe가 섞이지 않습니다.
- `--collect-all ccxt` : ccxt가 거래소별 모듈을 내부적으로 동적 로딩하기 때문에, 이 옵션을
  빼면 실행 시 `ModuleNotFoundError: No module named 'ccxt.gate'` 같은 오류가 납니다
- `--collect-all certifi` : TLS 인증서(cacert.pem)를 exe에 포함. 빼면
  `Could not find a suitable TLS CA certificate bundle` 오류로 거래소 연결이 전부 실패합니다 (필수)

`pyinstaller`가 인식되지 않는다고 나오면 위처럼 앞에 `python -m` 을 붙이면 됩니다.

빌드가 끝나면 같은 폴더에 `dist` 폴더가 생기고, 그 안에 `MartingaleBotMine.exe` 가 있습니다.

---

## 4단계: 실행

1. `dist/MartingaleBotMine.exe` 더블클릭
2. 거래소 선택 → API Key / Secret Key 입력 (OKX·Bitget은 Passphrase도 필요)
3. (선택) 텔레그램 봇 토큰 / Chat ID 입력
   - **그룹방으로 받으려면** 봇을 그룹에 초대한 뒤 `🔍 대화방 ID 찾기` 클릭
   - 여러 곳에 동시 전송하려면 쉼표로 구분 — 예: `66721231, -1004339079566`
   - `✈ 연결 테스트`로 실제 도착하는지 먼저 확인
4. 레버리지 / 포지션 크기 / 익절 / 물타기 간격 / 손절 입력
5. **`▶ 자동매매 시작`** 클릭 → 즉시 실제 주문이 나가는 자동매매 시작 (모의매매 아님)
6. 멈추려면 **`■ 정지`**

실행 후 로그 첫 줄에 `개인용 자동매매 v1.0.3` 이 찍히면 최신 파일로 빌드된 것입니다.

---

## 배포용과 무엇이 다른가

| | 배포용 | 개인용 |
|---|---|---|
| exe 이름 | `HedgedMartingaleBot.exe` | `MartingaleBotMine.exe` |
| 텔레그램 | 없음 | 있음 (그룹방·다중 전송 가능) |
| 익절/물타기/손절 % | 고정 | 직접 조정 가능 |
| 진입 차수 표시 | 안 보임 | `1차/2차...` 표시 |
| 상태창 | BTC 수량 기준 | USDT 금액 + 1~4차 예정가 |
| 상세 로그 | 없음 | 켤 수 있음 |
| 매매상태 초기화 | 없음 | 있음 |
| 창 크기 | 내용에 딱 맞음 | 30% 작게 + 전체 스크롤 |

### 파일이 분리되어 있어 서로 간섭하지 않습니다

| 종류 | 배포용 | 개인용 |
|---|---|---|
| 설정(API 키 등) | `.hedged_martingale_bot_gui_config.json` | `.martingale_bot_mine_config.json` |
| 매매 상태 | `.hedged_martingale_bot_state.json` | `.martingale_bot_mine_state.json` |
| 매매 기록 | `.hedged_martingale_bot_trades.json` | `.martingale_bot_mine_trades.json` |

모두 사용자 폴더(`C:\Users\사용자이름\`)에 저장됩니다.

⚠️ 상태 파일이 분리되어 있다는 것은, **한 프로그램에서 진행 중이던 매매를 다른 프로그램이
이어받지 않는다**는 뜻입니다. 포지션을 들고 있는 상태에서 배포용↔개인용을 갈아타지 마세요.
(갈아타더라도 거래소 실제 포지션을 읽어 복구하지만, 진행 단계 추정이 정확하지 않을 수 있습니다.)

---

## 프로그램을 껐다 켰을 때

매매 상태는 30초마다 자동 저장되고, 다시 켜면 **저장된 상태 + 거래소 실제 포지션**을
대조해서 이어서 매매합니다.

| 상황 | 동작 |
|---|---|
| 저장된 상태와 거래소가 일치 | 그대로 이어서 진행 |
| 상태 파일이 없거나 깨짐 | 백업(`.bak`) → 거래소 포지션 순으로 복구 |
| 거래소에서 수동으로 청산함 | 내부 상태를 비우고 새로 시작 |
| 거래소 조회 실패 | 저장된 상태를 그대로 사용 |

프로그램이 꺼져 있는 동안에는 **아무 매매도 하지 않습니다.** 물타기·익절·손절 모두
멈추므로, 포지션을 들고 있을 때 오래 꺼두지 마세요.

---

## 처음 실거래 전 체크리스트

- [ ] API 키에 **선물(Perpetual Futures) 거래 권한**이 켜져 있는지 확인
- [ ] 선물 지갑에 실제 매매할 USDT가 입금되어 있는지 확인
- [ ] 시작 시점에 **기존 포지션이나 미체결 주문이 없는지** 확인
      (양방향 모드/레버리지 자동 설정이 기존 포지션이 있으면 실패할 수 있음)
- [ ] `✈ 연결 테스트`로 텔레그램이 실제로 도착하는지 확인
- [ ] **소액으로 하루 이상** 돌려보고 로그가 의도대로 찍히는지 확인 후 자금 증액

---

## 자주 발생하는 문제

**Q. `pyinstaller` 용어가 인식되지 않습니다**
A. 명령 앞에 `python -m` 을 붙이세요. → `python -m PyInstaller --onefile ...`

**Q. exe 실행 시 `ModuleNotFoundError`가 뜹니다**
A. `--collect-all ccxt` 를 빼고 빌드했을 가능성이 높습니다. 3단계 명령어를 그대로 다시 실행하세요.

**Q. `Could not find a suitable TLS CA certificate bundle` 오류가 반복됩니다**
A. 인증서가 exe에 안 들어간 것입니다. `--collect-all certifi` 를 넣어 다시 빌드하세요.

**Q. 백신이 exe를 차단합니다**
A. PyInstaller로 만든 exe는 서명되지 않아 일부 백신이 오탐지하는 경우가 흔합니다.

**Q. 시작을 눌러도 계좌 연결 실패가 뜹니다**
A. API 키/시크릿 오타, 선물 거래 권한 미설정, 또는 방화벽 문제일 수 있습니다.
   `상세 로그`를 켜면 구체적인 오류 메시지가 나옵니다.

**Q. 텔레그램이 그룹방에 안 갑니다**
A. 봇을 그룹에 초대했는지, Chat ID가 `-100`으로 시작하는지 확인하세요.
   `🔍 대화방 ID 찾기`에 그룹이 안 보이면 @BotFather → `/setprivacy` → **Disable** 후
   그룹방에 메시지를 한 번 더 보내고 다시 눌러보세요.

**Q. 코드를 수정했는데 exe에 반영이 안 됩니다**
A. `dist`, `build` 폴더와 `.spec` 파일을 지우고 다시 빌드하세요. 실행 후 로그 첫 줄의
   버전 번호로 최신 여부를 확인할 수 있습니다.
