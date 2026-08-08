# 헤르메스 (Hermes) — 자동매매 봇 제어 계층

기존 매매 봇은 GUI 버튼으로만 조작할 수 있었습니다. 헤르메스는 그 위에 얇은 **제어 계층**을 얹어
GUI 밖에서도 봇을 다룰 수 있게 합니다.

| 기능 | 설명 |
|---|---|
| 시작 / 정지 | 원격에서 매매 루프 기동·정지 |
| 일시정지 / 재개 | **신규 진입만** 차단. 보유 포지션의 트레일링 청산 관리는 계속됨 |
| 상태 조회 | 상태·포지션·잔고·최근 제어 이력 |
| 실시간 설정 변경 | 레버리지·주문 금액 등을 **봇 재시작 없이** 변경 |
| 긴급 청산 (킬 스위치) | 보유 포지션 전량 시장가 청산 후 정지 |

추가 의존성은 없습니다. 표준 라이브러리만 사용합니다.

---

## 구조

```
hermes/
  state.py           상태 머신 (stopped/starting/running/paused/stopping/error)
  config.py          실행 중 변경 가능한 설정 저장소 (검증 포함, 스레드 안전)
  backend.py         매매 엔진 인터페이스 (Protocol)
  agent.py           제어 명령의 단일 진입점 + 감사 로그
  commands.py        문자열 명령 해석 (HTTP·텔레그램·CLI 공용)
  control_server.py  토큰 인증 로컬 HTTP API
  adapter.py         기존 tkinter 봇 ↔ backend.py 연결
```

핵심 설계는 **에이전트가 GUI를 모른다**는 점입니다. 에이전트는 `TradingBackend` 인터페이스만
알기 때문에, 거래소나 tkinter 없이 전부 테스트할 수 있습니다.

---

## 붙이는 방법

`gateio_supertrend_bot.py` 마지막의 실행부에 두 줄만 추가하면 됩니다.

```python
if __name__ == "__main__":
    if not ensure_license_active():
        sys.exit(1)
    root = tk.Tk()
    app = GateioProSuperTrendBot(root)

    from hermes import attach
    control = attach(app, port=8787, leverage=5, amount_mode="fixed", fixed_amount_usdt=100)

    start_periodic_recheck(root)
    root.mainloop()
```

`attach()`는 **반드시 UI 스레드**(봇을 만든 그 스레드)에서 호출해야 합니다. 어댑터가 이 시점의
스레드를 tkinter 메인 스레드로 기억해 두고, 이후 원격 명령을 그 스레드로 넘기기 때문입니다.

붙이지 않으면 봇은 기존과 **완전히 동일하게** 동작합니다. `runtime_config`가 `None`이면
매매 루프는 예전처럼 시작 시점 설정값을 그대로 씁니다.

### 접속 토큰

토큰 우선순위는 `attach(token=...)` → 환경변수 `HERMES_TOKEN` → 자동 생성입니다.
자동 생성된 토큰은 봇 로그창에 한 번 출력됩니다. 고정 토큰을 쓰려면:

```bash
set HERMES_TOKEN=내가-정한-긴-토큰    # Windows
export HERMES_TOKEN=my-long-token     # macOS/Linux
```

토큰에는 **ASCII 문자만** 쓸 수 있습니다 (HTTP 헤더 제약). 한글 토큰은 시작 시 거부됩니다.

---

## HTTP API

기본 주소는 `http://127.0.0.1:8787` 입니다.

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/health` | 인증 불필요. 서버 생존 확인 |
| GET | `/status` | 상태·포지션·잔고 |
| GET | `/params` | 설정 항목과 현재 값 |
| GET | `/log?limit=20` | 제어 명령 이력 |
| POST | `/command` | `{"command": "...", "args": [...]}` |

```bash
TOKEN=...   # 봇 로그창에 출력된 토큰

curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8787/status

curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"command":"set","args":["leverage","10"]}' \
     http://127.0.0.1:8787/command

curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"command":"kill","args":["CONFIRM","장 급락"]}' \
     http://127.0.0.1:8787/command
```

응답의 `ok` 필드로 성공 여부를 판단합니다. **명령이 거부된 경우에도 HTTP 200**이 오고
`ok: false`가 담깁니다 (요청 자체는 올바르게 처리된 것이므로). 400은 요청이 잘못된 경우,
401은 토큰 문제입니다.

---

## 명령 목록

| 명령 | 설명 |
|---|---|
| `status [local]` | 상태 조회. `local`을 붙이면 거래소 조회를 생략 |
| `params` | 설정 가능한 항목과 현재 값 |
| `log [개수]` | 최근 제어 명령 이력 |
| `start` | 자동매매 시작 |
| `stop` | 정지 (**포지션은 그대로 유지**) |
| `pause` | 신규 진입만 차단 |
| `resume` | 신규 진입 재개 |
| `set <항목> <값>` | 설정 실시간 변경 |
| `kill CONFIRM [사유]` | 긴급 청산 + 정지 |
| `reset` | 오류/킬스위치 잠금 해제 |

### 변경 가능한 설정

| 항목 | 허용 범위 |
|---|---|
| `leverage` | 1 ~ 125 |
| `amount_mode` | `fixed` \| `balance_pct` |
| `fixed_amount_usdt` | 0 초과 |
| `balance_pct` | 0 초과 100 이하 |
| `poll_sec` | 3 ~ 300 |
| `entries_enabled` | `true` \| `false` (pause/resume가 자동으로 조작) |

설정 변경은 **다음 판단 주기**(기본 10초)부터 적용됩니다. 이미 열려 있는 포지션에는
소급 적용되지 않습니다 — 레버리지를 바꿔도 다음 진입부터 반영됩니다.

---

## 알아둘 동작

**일시정지의 의미.** `pause`는 루프를 멈추지 않습니다. 신규 진입만 막고, 보유 포지션의
ATR 트레일링 청산은 계속 동작합니다. "더 들어가진 말고, 갖고 있는 건 계속 지켜봐라"에 해당합니다.

일시정지 중에 추세 전환이 오면 그 신호는 **건너뜁니다**. `resume` 후 곧바로 진입하지 않고
다음 추세 전환을 기다립니다. 이미 지나간 신호로 뒤늦게 들어가는 것을 막기 위함입니다.

**킬 스위치는 잠깁니다.** 실행 후 에이전트가 잠기며 `reset` 전에는 `start`가 거부됩니다.
자동 재기동으로 방금 정리한 포지션에 다시 들어가는 사고를 막기 위한 안전장치입니다.
청산이 실패해도 매매 정지는 반드시 수행하고, 결과를 `ok: false`로 알려줍니다 —
이 경우 **거래소에서 포지션을 직접 확인**하세요.

**정지는 청산이 아닙니다.** `stop`은 루프만 멈춥니다. 포지션은 거래소에 그대로 남습니다.
포지션까지 정리하려면 `kill`을 쓰세요.

**외부 노출은 기본 차단.** 이 API는 실거래 계좌를 움직입니다. `127.0.0.1` 외의 주소에
바인딩하려면 `allow_remote=True`를 명시해야 하며, 그 경우 방화벽과 강력한 토큰이 필수입니다.
외부에서 접근해야 한다면 API를 여는 대신 SSH 터널을 권장합니다.

---

## 테스트

```bash
python -m unittest discover -s tests -v
```

거래소 연결도, tkinter도 필요 없습니다. 가짜 백엔드로 제어 로직만 검증합니다 (48개).

---

## exe 빌드

봇 본체는 `hermes`를 import하지 않으므로 현재 `.spec` 그대로 빌드해도 문제없습니다.
위 예시처럼 `attach()`를 실행부에 추가한 뒤에도 PyInstaller가 정적 분석으로 패키지를 찾지만,
혹시 누락되면 `.spec`의 `hiddenimports`에 추가하세요.

```python
hiddenimports=['hermes', 'hermes.adapter'],
```
