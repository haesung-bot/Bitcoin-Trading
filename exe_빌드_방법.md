# exe 파일 만드는 방법 (Windows PC에서 직접 진행)

⚠️ 중요: exe 빌드는 반드시 **Windows PC**에서 해야 합니다.
빌드 도구(PyInstaller)는 빌드하는 컴퓨터의 운영체제용 실행파일만 만들 수 있어서,
Mac이나 Linux에서 만들면 Windows에서 실행이 안 됩니다.

---

## 1단계: 폴더 준비

바탕화면 등에 새 폴더(예: `매매봇`)를 만들고, 아래 2개 파일을 그 안에 넣습니다.

- `gateio_supertrend_bot.py`
- `license_client_code.py`

⚠️ 두 파일은 **반드시 같은 폴더**에 있어야 합니다.

---

## 2단계: license_client_code.py 서버 주소 수정

`license_client_code.py`를 메모장으로 열어서 이 줄을 찾습니다.

```python
LICENSE_SERVER_URL = "https://YOUR_SERVER_DOMAIN_OR_IP:5000"
```

`YOUR_SERVER_DOMAIN_OR_IP:5000` 부분을 실제로 운영 중인 라이선스 서버 주소로 바꿉니다.
(license_code_server.py를 올려둔 서버 주소)

---

## 3단계: 필요한 프로그램 설치

cmd(명령 프롬프트)를 열고 순서대로 입력합니다.

```
cd 매매봇폴더경로
pip install pyinstaller ccxt pandas requests
```

---

## 4단계: exe 빌드

같은 cmd 창에서:

```
pyinstaller --onefile --noconsole --name "GateSuperTrendBot" --add-data "license_client_code.py;." gateio_supertrend_bot.py
```

- `--onefile` : 파일 하나로 묶기
- `--noconsole` : 실행 시 검은 콘솔창 안 뜨게 하기 (GUI만 뜸)
- `--name` : 만들어질 exe 파일 이름
- `--add-data` : license_client_code.py를 exe 안에 함께 포함

빌드가 끝나면 같은 폴더 안에 `dist` 폴더가 생기고, 그 안에 `GateSuperTrendBot.exe` 파일이 있습니다.

---

## 5단계: 테스트

1. `dist/GateSuperTrendBot.exe`를 더블클릭
2. 활성화 코드 입력창이 뜨는지 확인
3. `license_code_server.py`에서 미리 발급해둔 테스트 코드를 입력해서 정상적으로 프로그램이 실행되는지 확인

---

## 배포 전 최종 체크리스트

- [ ] `license_code_server.py`를 본인 서버에 올려서 24시간 실행 중인지 확인
- [ ] `SERVER_SECRET`, `ADMIN_SECRET`을 랜덤값으로 교체했는지 확인
- [ ] `license_client_code.py`의 `LICENSE_SERVER_URL`이 정확한 서버 주소인지 확인
- [ ] 테스트 코드로 정상 작동 확인 후, 테스트 코드는 `/admin/block` 처리
- [ ] `dist/GateSuperTrendBot.exe` 파일만 사용자에게 배포 (다른 파일은 필요 없음)

## 자주 발생하는 문제

**Q. exe 실행 시 "ModuleNotFoundError"가 뜬다**
A. pip install 할 때 `ccxt`, `pandas`, `requests` 중 하나가 빠졌을 가능성이 높습니다. 3단계를 다시 확인하세요.

**Q. 백신 프로그램이 exe를 위험하다고 차단한다**
A. PyInstaller로 만든 exe는 서명되지 않은 파일이라 일부 백신이 오탐지하는 경우가 흔합니다.
   배포 시 사용자에게 미리 안내하거나, 코드 서명 인증서를 구매해 서명하면 줄어듭니다 (선택사항, 유료).

**Q. exe 용량이 너무 크다 (100MB 이상)**
A. pandas, ccxt 라이브러리가 원래 용량이 큽니다. `--onefile` 대신 `--onedir`로 만들면
   실행은 폴더째 배포해야 하지만 실행 속도는 더 빠릅니다.
