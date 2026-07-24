# hedged_martingale_bot.exe 만드는 방법 (Windows PC에서 직접 진행)

⚠️ 중요: exe 빌드는 반드시 **Windows PC**에서 해야 합니다.
빌드 도구(PyInstaller)는 빌드하는 컴퓨터의 운영체제용 실행파일만 만들 수 있어서,
Linux 서버(구글 클라우드 등)에서는 만들 수 없고, 만들어진 exe도 Windows에서만 실행됩니다.
"구글 서버에서 돌린다"고 하셔도, 그 서버가 Windows Server가 아니라면 exe는 그 서버에서
실행할 수 없습니다 — 이 경우 본인 Windows PC에서 exe를 만든 뒤, 그 PC(또는 다른 Windows
Server VM)에서 실행하셔야 합니다.

---

## 1단계: 폴더 준비

바탕화면 등에 새 폴더(예: `헷지마틴게일봇`)를 만들고, 아래 2개 파일을 그 안에 넣습니다.

- `hedged_martingale_bot.py`   (전략 엔진, 실거래 로직)
- `hedged_martingale_bot_gui.py`   (버튼 눌러서 시작하는 화면)

⚠️ 두 파일은 **반드시 같은 폴더**에 있어야 합니다 (gui 파일이 core 파일을 import 합니다).

---

## 2단계: 필요한 프로그램 설치

cmd(명령 프롬프트)를 열고 순서대로 입력합니다.

```
cd 헷지마틴게일봇_폴더경로
pip install pyinstaller ccxt requests
```

(tkinter는 Windows용 파이썬에 기본 포함되어 있어 따로 설치할 필요 없습니다.)

---

## 3단계: exe 빌드

같은 cmd 창에서:

```
pyinstaller --onefile --noconsole --name "HedgedMartingaleBot" --collect-all ccxt hedged_martingale_bot_gui.py
```

- `--onefile` : 파일 하나로 묶기
- `--noconsole` : 실행 시 검은 콘솔창 안 뜨게 하기 (GUI 창만 뜸)
- `--name` : 만들어질 exe 파일 이름
- `--collect-all ccxt` : ccxt가 거래소별 모듈을 내부적으로 동적 로딩하기 때문에, 이 옵션을
  빼면 exe 실행 시 "ModuleNotFoundError: No module named 'ccxt.gate'" 같은 오류가 날 수 있음

빌드가 끝나면 같은 폴더 안에 `dist` 폴더가 생기고, 그 안에 `HedgedMartingaleBot.exe` 파일이 있습니다.

---

## 4단계: 실행

1. `dist/HedgedMartingaleBot.exe`를 더블클릭
2. 화면에 Gate.io API Key / Secret 입력
3. (선택) 텔레그램 봇 토큰 / Chat ID 입력 — 넣으면 진입/익절/손절마다 알림이 옴
4. **"▶ 매매 시작 (실거래)"** 버튼 클릭 → 확인 팝업에서 "예" 선택 시 즉시 실제 주문이 나가는
   자동매매가 시작됨 (모의매매 아님)
5. 멈추려면 **"■ 정지"** 버튼 클릭

입력한 API 키/텔레그램 정보는 실행 PC의 `사용자폴더\.hedged_martingale_bot_gui_config.json`에
암호화 없이 저장되어, 다음에 exe를 켜면 자동으로 채워집니다. 여러 사람이 쓰는 PC라면
주의하세요.

---

## 처음 실거래 시작 전 체크리스트

- [ ] Gate.io API 키에 **선물(Perpetual Futures) 거래 권한**이 켜져 있는지 확인
- [ ] Gate.io 선물 지갑에 실제 매매할 자금(USDT)이 입금되어 있는지 확인
- [ ] 매매 시작 시점에 **기존에 열려있는 포지션이나 미체결 주문이 없는지** 확인
      (Dual Mode/레버리지 자동 설정이 기존 포지션이 있으면 실패할 수 있음)
- [ ] 소액으로 먼저 하루 이상 실제 실행해보고 텔레그램 알림이 정상적으로 오는지, 로그에
      의도한 대로 진입/청산이 찍히는지 확인 후 자금을 늘리는 것을 권장

## 자주 발생하는 문제

**Q. exe 실행 시 "ModuleNotFoundError"가 뜬다**
A. `--collect-all ccxt` 옵션을 빼고 빌드했을 가능성이 높습니다. 3단계 명령어를 그대로
   다시 실행하세요.

**Q. 백신 프로그램이 exe를 위험하다고 차단한다**
A. PyInstaller로 만든 exe는 서명되지 않은 파일이라 일부 백신이 오탐지하는 경우가 흔합니다.

**Q. "매매 시작"을 눌러도 계좌 연결 실패 로그가 뜬다**
A. API 키/시크릿 오타, Gate.io API 키의 선물 거래 권한 미설정, 또는 PC의 인터넷 연결/방화벽
   문제일 수 있습니다. 로그창에 나오는 구체적인 오류 메시지를 확인하세요.
