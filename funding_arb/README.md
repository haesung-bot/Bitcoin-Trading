# 펀딩비 차익거래(Funding Rate Arbitrage) 백테스터

두 거래소(또는 현물/무기한선물) 간 **펀딩비 격차**를 이용한 델타 중립 전략의
백테스트 엔진. 펀딩 수익 단순 합산이 아니라 **거래수수료 · 슬리피지 · 가격갭(베이시스)
변동**을 모두 차감한 순수익(Net PnL)을 산출한다.

## 1. 실행

```bash
python3 funding_arb/backtest.py --data funding_arb/sample_data.csv
```

주요 옵션

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--data` | `funding_arb/sample_data.csv` | 입력 CSV 경로 |
| `--funding-unit` | `decimal` | `decimal`=0.0015, `percent`=0.15 |
| `--notional` | `10000` | 레그당 평균 명목금액(USD) |
| `--capital` | `notional` | 수익률 산정 기준 자본 |
| `--fee-a` / `--fee-b` | `0.0005` | 거래소별 편도 테이커 수수료 |
| `--slippage` | `0.0004` | **왕복 총** 슬리피지 |
| `--entry-threshold` | `0.0015` | 진입 기준 `|f_A - f_B|` |
| `--exit-threshold` | `0.0005` | 청산 기준 `|f_A - f_B|` |
| `--max-hold` | `9` | 최대 보유 회차(9회 = 3일) |
| `--collect-entry-interval` | off | 진입 회차 펀딩도 수령 처리 |
| `--export` | - | 구간별 결과 CSV 저장 |

## 2. 입력 데이터 형식

```csv
ts,price_a,price_b,funding_a,funding_b
2026-08-01 00:00,68000,67980,0.0022,-0.0004
2026-08-01 08:00,68420,68405,0.0019,-0.0003
```

- `ts` : 펀딩 정산 시각 (8시간 간격 권장). `시각`, `time`, `datetime` 등 별칭 허용
- `price_a` / `price_b` : 해당 시각 A/B 거래소 가격
- `funding_a` / `funding_b` : 해당 시각에 **정산되는** 펀딩비 (부호 포함)
- 한글 헤더(`시각,A거래소가격,B거래소가격,A펀딩비,B펀딩비`)도 자동 인식

## 3. 산식

### 진입 방향
```
f_spread(t) = funding_a(t) - funding_b(t)

f_spread >= +entry_threshold  ->  A 숏 / B 롱   (dir_a = -1)
f_spread <= -entry_threshold  ->  A 롱 / B 숏   (dir_a = +1)
```
숏은 펀딩비 양수일 때 수령, 롱은 양수일 때 지급.

### 수량 (델타 중립)
```
mid_entry = (P_a_entry + P_b_entry) / 2
Q         = notional / mid_entry          # 양 거래소 동일 수량
```

### 회차당 펀딩 순수익
```
rate(t)        = -dir_a * ( f_a(t) - f_b(t) )   # 진입 조건상 항상 +|f_spread|
Net_Funding(t) = notional * rate(t)
```

### 가격갭(베이시스) 손익
```
S(t) = P_a(t) - P_b(t)

구간 증분 = Q * dir_a * ( S(t) - S(t-1) )
누적      = Q * dir_a * ( S_exit - S_entry )
```
A숏/B롱(`dir_a=-1`)은 갭이 **축소**될수록 이익, **확대**되면 손실.

### 비용
```
진입 수수료 = Q*P_a_entry*fee_a + Q*P_b_entry*fee_b
청산 수수료 = Q*P_a_exit *fee_a + Q*P_b_exit *fee_b     # 총 4회 체결
진입 슬리피지 = (slippage_round_trip / 2) * (Q * mid_entry)
청산 슬리피지 = (slippage_round_trip / 2) * (Q * mid_exit)
```
기본값 기준 왕복 총비용 = `0.05% x 4 + 0.04% = 0.24%`
→ **손익분기 펀딩 누적치 = 0.24%**. 8h당 0.15% 격차라면 최소 2회차는 보유해야 본전.

### 최종 순수익
```
Net PnL = 총 수령 펀딩비 - 총 거래수수료 - 총 슬리피지 + 베이시스 손익
```

## 4. 룩어헤드 방지 규약

- `funding_x(t)` 는 시각 `t` 에 정산되는 펀딩비.
- 시각 `t` 의 펀딩비를 관측 → **시각 `t` 종가에 진입 결정**.
- **진입 회차(t0)의 펀딩비는 수령하지 않는다.** `t0+1` 회차부터 수령.
  (거래소 predicted funding rate 기반으로 미리 진입하는 모델이라면
   `--collect-entry-interval` 로 변경)
- 청산 회차의 펀딩비는 정산 직후 청산으로 보아 수령/지급에 포함.

## 5. 청산 조건 (우선순위 순)

1. `|f_spread| <= exit_threshold` — 격차 축소
2. `f_spread` 부호 반전 — 펀딩 방향 반전
3. 보유 회차 `>= max_hold` — 목표 보유기간 만료
4. 데이터 종료 — 강제 청산

## 6. 출력

1. **구간별 시뮬레이션 표** — 회차별 펀딩 / 베이시스 / 비용 / 순증감 / 누적 / DD
2. **트레이드별 손익 분해** — 펀딩수익 · 수수료 · 슬리피지 · 갭손익 · 순손익 · 청산사유
3. **최종 리포트** — Net PnL, 순수익률, 단순 연환산 APR, MDD, 트레이드 수, 승률,
   평균 손익, Profit Factor, 포지션 노출도

## 7. 현재 모델에 반영되지 않은 위험 (실전 적용 전 확인 필요)

- **청산(liquidation) 리스크** : 레그별 증거금 분리 관리, 가격 급변 시 한쪽 강제청산
- **펀딩비 예측 오차** : 정산 직전 펀딩비 변동
- **호가 깊이** : 명목금액이 커질수록 슬리피지가 고정값이 아닌 함수
- **출금/전송 지연** : 거래소 간 증거금 리밸런싱 시간과 수수료
- **거래소 리스크** : API 장애, 출금 중단, 거래소 신용
