# ICT High-Probability FVG Strategy

CCXT / Pandas 기반 ICT FVG 전략과 백테스팅 코드입니다.

```
ict_fvg/
  config.py      설정값 (타임프레임, 필터, 손익비, 자금관리)
  data.py        CCXT OHLCV 수집 + CSV 캐싱
  structure.py   스윙, 유동성 사냥, MSS, 프리미엄/디스카운트
  fvg.py         FVG 탐지 및 생애주기
  backtest.py    백테스트 엔진 + 성과 지표
  strategy.py    실시간 신호 생성 (백테스트와 동일 로직)
run_ict_backtest.py    실행 CLI
tests/test_ict_fvg.py  검증 테스트 14개
```

---

## 설치 및 실행

```bash
pip install -r requirements-ict.txt

# 기본 백테스트 (HTF 1시간 / LTF 15분)
python run_ict_backtest.py --start 2024-01-01

# 일봉 구역 + 5분봉 진입
python run_ict_backtest.py --htf 1d --ltf 5m --start 2024-01-01 --end 2024-12-31

# 지금 대기 중인 진입 자리만 확인
python run_ict_backtest.py --start 2024-06-01 --scan-only

# 검증 테스트
python tests/test_ict_fvg.py
```

---

## 전략 규칙 구현 위치

### 1. 타임프레임

| 구분 | 용도 | 기본값 | 설정 |
|---|---|---|---|
| HTF | 스윙 · 구역 판단 | `1h` | `--htf 1h` / `1d` |
| LTF | 진입 타점 | `15m` | `--ltf 5m` / `15m` |

### 2. FVG 탐지 — `fvg.py: detect_fvgs()`

| | 조건 | 상단 | 하단 | CE |
|---|---|---|---|---|
| 상승 FVG | `Candle[i-2].high < Candle[i].low` | `Candle[i].low` | `Candle[i-2].high` | 중간값 |
| 하락 FVG | `Candle[i-2].low > Candle[i].high` | `Candle[i-2].low` | `Candle[i].high` | 중간값 |

`Candle[i-2].high == Candle[i].low`처럼 딱 붙은 경우는 갭이 아닙니다(부등호가 `<`, `>`).

### 3. 필터링 — `structure.py` + `fvg.py`

**프리미엄 / 디스카운트**
HTF 딜링 레인지의 0.5 레벨(균형점)을 기준으로 나눕니다.
- 롱: FVG가 균형점 **아래**(디스카운트)에 있어야 함
- 숏: FVG가 균형점 **위**(프리미엄)에 있어야 함

`--zone-mode ce`는 CE만, `--zone-mode full`은 갭 전체가 구역 안에 들어와야 통과합니다.

**외부 유동성 돌파 후에만 활성화**
"레인지 내부 FVG 무시" 규칙을 구현하려면 **유동성 사냥**과 **진짜 이탈**을 구분해야 합니다. 이 코드는 종가로 판단합니다:

| 상황 | 종가 위치 | 해석 |
|---|---|---|
| 스윙 저점 아래로 내려감 | 저점 **위**로 회복 | 매도측 유동성 **사냥** → 상승 시나리오 시작 |
| 스윙 저점 아래로 내려감 | 저점 **아래** | 진짜 하락 **이탈** → 상승 시나리오 폐기 |
| 스윙 고점 위로 올라감 | 고점 **아래**로 회복 | 매수측 유동성 **사냥** → 하락 시나리오 시작 |
| 스윙 고점 위로 올라감 | 고점 **위** | 진짜 상승 **이탈** → 상승 MSS 성립 |

이렇게 3단계로 셋업이 진행됩니다:

```
0단계  없음
  ↓   유동성 사냥 발생
1단계  MSS 대기
  ↓   반대편 스윙을 종가로 돌파 (구조 전환)
2단계  진입 대기  ← 이 구간에서 생긴 FVG만 후보로 인정
```

`--no-sweep`으로 필터를 끄면 레인지 내부 FVG까지 전부 잡힙니다(비교용).

### 4. 진입 / 손절 / 익절 — `backtest.py: _try_entry()`

- **진입**: 가격이 FVG의 CE(0.5)에 터치할 때. 지정가 체결로 가정합니다.
- **손절**: `--sl-mode`
  - `fvg` FVG 반대편 끝
  - `swing` 직전 LTF 스윙 바깥
  - `wider` 둘 중 더 먼 쪽 (기본, 가장 보수적)
  - 여기에 ATR 기준 여유(`sl_buffer_atr`)를 더합니다.
- **익절**: 직전 HTF 스윙 고점(롱) / 저점(숏)
- **R:R 필터**: `--min-rr 2` 미만이면 **주문 자체를 넣지 않습니다.**

---

## 미래참조(Lookahead) 차단

백테스트가 실전과 달라지는 가장 큰 원인입니다. 이 코드는 세 곳에서 막습니다.

**1. 스윙 확정 지연**
스윙 고점은 오른쪽 N봉이 마감돼야 확정됩니다. 차트를 눈으로 볼 땐 당연해 보이지만 실시간으로는 N봉 뒤에야 알 수 있습니다. 모든 스윙은 발생 시점(`i`)이 아니라 확정 시점(`i + right`)부터만 사용됩니다.

**2. HTF 봉 마감 대기**
HTF 봉은 마감된 뒤에야 알 수 있습니다. 시가 시각이 아니라 **종가 시각** 기준으로 LTF에 붙입니다. 이걸 시가 시각으로 붙이면 아직 오지도 않은 1시간치 정보를 15분봉이 미리 보게 되어 수익률이 크게 부풀려집니다.

**3. FVG 확정 대기**
FVG는 세 번째 봉이 마감돼야 확정되므로, 진입 가능한 가장 빠른 시점은 그 다음 봉입니다.

### 검증 방법

`tests/test_ict_fvg.py`의 마지막 테스트가 이걸 직접 검증합니다. 데이터를 뒤에서 잘라내고 다시 돌렸을 때 **과거 거래가 한 건이라도 달라지면 미래참조가 있다는 뜻**입니다.

실제 BTC 데이터(OKX, 2024-01~07, 15분봉 17,472개)로 40% / 60% / 80% 지점에서 잘라 검증한 결과, 세 경우 모두 진입시각·진입가·손절가·목표가·청산시각·청산가가 완전히 일치했습니다.

---

## 딜링 레인지 정의에 대해

"가장 최근 확정 스윙 고점"과 "가장 최근 확정 스윙 저점"을 각각 독립적으로 가져오면, 두 스윙이 시간상 짝이 안 맞을 때 현재가가 레인지 밖으로 나가버립니다.

```
레인지 27,720 ~ 27,890   (폭 170포인트)
현재가  25,849            ← 레인지 한참 아래
```

이 상태의 0.5 레벨은 프리미엄/디스카운트 판정에 아무 의미가 없습니다. 그래서 **스윙 확정 이후 가격이 실제로 이탈한 만큼 레인지를 넓혀줍니다.** 이미 마감된 봉의 고가/저가만 쓰므로 미래참조가 아니며, 새 스윙이 확정되면 그 방향 경계는 다시 잡힙니다.

추가로 레인지 폭이 HTF ATR의 `min_range_atr`배보다 좁으면 균형점을 `NaN`으로 두어 신호를 내지 않습니다.

---

## 백테스트 결과 해석 시 주의

이 엔진은 다음을 **보수적으로** 가정합니다.

- 한 봉에서 손절가와 익절가에 모두 닿으면 **항상 손절 먼저**로 계산합니다. 봉 안의 가격 순서는 OHLC만으로 알 수 없기 때문입니다. 낙관적으로 잡으면 결과가 거짓으로 좋아집니다.
- 손절은 시장가(taker) + 슬리피지, 진입/익절은 지정가(maker) 수수료를 적용합니다.

반대로 다음은 **낙관적**입니다.

- 진입은 가격이 CE를 스치기만 하면 체결된 것으로 봅니다. 실제로는 호가 대기열 때문에 체결되지 않을 수 있습니다.
- 자금조달 수수료(Funding Fee), 부분 체결, 거래소 장애는 반영하지 않습니다.

**즉 나온 수익률은 상한선에 가깝습니다.** 참고로 실제 BTC 6개월 구간(OKX 2024-01~07, 기본 설정)에서는 17거래 / 승률 35% / PF 1.18 / 기대값 0.17R 로, 수수료를 겨우 넘기는 수준이었습니다. 파라미터와 구간에 따라 결과가 크게 달라지므로, 실전 투입 전 여러 구간에서 검증하고 반드시 데모 계정으로 먼저 돌려보세요.

---

## 실시간 매매 연동

`strategy.py`가 백테스트와 **동일한 함수**로 신호를 만듭니다. 로직이 갈라지면 백테스트가 무의미해지기 때문입니다.

```python
from ict_fvg import StrategyConfig, fetch_ohlcv, scan_setups, drop_unclosed_bar

cfg = StrategyConfig(symbol="BTC/USDT", htf="1h", ltf="15m")

ltf = drop_unclosed_bar(fetch_ohlcv("okx", "BTC/USDT:USDT", "15m", start="2025-01-01"), "15m")
htf = drop_unclosed_bar(fetch_ohlcv("okx", "BTC/USDT:USDT", "1h",  start="2025-01-01"), "1h")

setups = scan_setups(ltf, htf, cfg, equity=10_000)
for _, s in setups.iterrows():
    if s["triggered"]:
        print(f"{s['direction']} 진입 {s['entry']:.1f} / 손절 {s['stop']:.1f} "
              f"/ 목표 {s['target']:.1f} / RR {s['rr']:.2f} / 수량 {s['qty']:.4f}")
```

`drop_unclosed_bar()`는 아직 마감되지 않은 봉을 잘라냅니다. 진행 중인 봉을 넣으면 고가/저가가 계속 변해서 신호가 생겼다 사라졌다 합니다.

지금 왜 신호가 없는지 확인하려면 `describe_market_state()`를 쓰거나 `--scan-only`로 실행하세요. 백테스트 출력 하단의 "진입 무산 사유" 집계도 어느 필터에서 막히는지 보여줍니다.

---

## 주요 옵션

```
--exchange / --symbol / --market-type   거래소, 심볼, 선물/현물
--htf / --ltf                           타임프레임
--start / --end                         기간
--ltf-csv / --htf-csv                   CSV로 실행 (네트워크 미사용)

--no-sweep          유동성 사냥 필터 해제
--no-mss            MSS 확인 생략 (사냥만으로 진입)
--zone-mode         ce | full
--min-fvg-atr       ATR 대비 최소 FVG 크기 (0이면 해제)

--min-rr            최소 손익비 (기본 2.0)
--sl-mode           fvg | swing | wider
--tp-target         nearest | range_high
--long-only / --short-only

--capital / --risk-pct / --max-leverage
--maker-fee / --taker-fee / --slippage-bps
--scan-only         백테스트 없이 현재 진입 자리만 출력
```

전체 목록은 `python run_ict_backtest.py --help`.
