# -*- coding: utf-8 -*-
"""최종 검증 1 — 매매 엔진 핵심 로직."""
import sys, os, math, json, logging, random
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from v_common import *

import hedged_martingale_bot as core
from hedged_martingale_bot import (HedgedMartingaleBot, Indicators, Side, Fill,
                                   TelegramNotifier, PaperBroker, parse_chat_ids,
                                   martingale_ladder)

core.logger.setLevel(logging.CRITICAL)
CLOCK = Clock()
core.time = CLOCK


def bot_with(ex=None, sp=None, **kw):
    defaults(core)
    for k, v in kw.items():
        setattr(core, k, v)
    ex = ex or Exchange()
    return HedgedMartingaleBot(ex, Notifier(), "V", state_path=sp), ex


# ══════════════════════════════════════════════════════════
section("1. 지표 (RSI / 볼린저 / EMA)")

eq("RSI 단조상승 = 100", round(Indicators.rsi([100 + i for i in range(50)]), 6), 100.0)
check("RSI 단조하락 ≈ 0", Indicators.rsi([100 - i for i in range(50)]) < 1e-6)
check("RSI 데이터부족 None", Indicators.rsi([1, 2, 3]) is None)
random.seed(3)
v = [50.0]
for _ in range(600):
    v.append(max(1.0, v[-1] * (1 + random.gauss(0, 0.012))))
rs = [Indicators.rsi(v[i - 100:i]) for i in range(100, len(v))]
check("RSI 항상 0~100", all(0 <= r <= 100 for r in rs), f"{min(rs):.1f}~{max(rs):.1f}")

d = [float(x) for x in range(1, 21)]
mid, up, lo = Indicators.bollinger(d)
m0 = sum(d) / 20
sd = math.sqrt(sum((x - m0) ** 2 for x in d) / 20)
eq("BB 중심", round(mid, 9), round(m0, 9))
eq("BB 상단", round(up, 9), round(m0 + 2 * sd, 9))
eq("BB 하단", round(lo, 9), round(m0 - 2 * sd, 9))
check("BB 상수열 폭0", Indicators.bollinger([100.0] * 20) == (100.0, 100.0, 100.0))
check("BB 데이터부족 None", Indicators.bollinger([1.0] * 5) is None)
check("BB 하단≤중심≤상단", all((lambda b: b[2] <= b[0] <= b[1])(Indicators.bollinger(v[i - 30:i]))
                          for i in range(30, 300)))

check("EMA 데이터부족 None", Indicators.ema([1, 2], 10) is None)
check("EMA 기간0 None", Indicators.ema([1.0] * 50, 0) is None)
check("EMA 상수열≈값", abs(Indicators.ema([100.0] * 300, 200) - 100.0) < 1e-6)
check("EMA 상승열 < 현재가", Indicators.ema([100.0 + i for i in range(300)], 200) < 399.0)
check("EMA 하락열 > 현재가", Indicators.ema([400.0 - i for i in range(300)], 200) > 101.0)

# ══════════════════════════════════════════════════════════
section("2. 진입 조건 + 추세 필터")

b, _ = bot_with()
L, S = b.long, b.short
bb = (100.0, 110.0, 90.0)
check("롱 RSI≤40 진입", L._entry_signal(100, 40.0, bb))
check("롱 RSI 41 미진입", not L._entry_signal(100, 41.0, bb))
check("롱 BB하단 터치 진입", L._entry_signal(90.0, 99.0, bb))
check("숏 RSI≥60 진입", S._entry_signal(100, 60.0, bb))
check("숏 RSI 59 미진입", not S._entry_signal(100, 59.0, bb))
check("숏 BB상단 터치 진입", S._entry_signal(110.0, 10.0, bb))
check("추세필터: 상승추세 숏차단", not S._entry_signal(100, 70, bb, 90.0))
check("추세필터: 상승추세 롱허용", L._entry_signal(100, 30, bb, 90.0))
check("추세필터: 하락추세 롱차단", not L._entry_signal(100, 30, bb, 110.0))
check("추세필터: 하락추세 숏허용", S._entry_signal(100, 70, bb, 110.0))
check("신호없으면 추세무관 미진입", not L._entry_signal(100, 70, bb, 90.0))

# ══════════════════════════════════════════════════════════
section("3. 마틴게일 사다리")

b, ex = bot_with()
L = b.long
P = 60000.0
base = ex.quantize_qty(ex.balance * core.LEVERAGE * core.INITIAL_MARGIN_PCT / P, P)
L._enter_initial(P)
eq("1차 수량 = 잔고×레버×포지션%/가격", L.total_qty, base)
prev = L.total_qty
for stage in (2, 3, 4):
    t = L.avg_price * (1 - core.STEP_TRIGGER_PCT - 1e-9)
    L.on_tick(t, 50, (t, t * 1.1, t * 0.9), CLOCK.time())
    eq(f"{stage}차 step", L.step, stage)
    eq(f"{stage}차 추가수량 = 직전×2", round(L.fills[-1].qty, 10), round(prev * 2, 10))
    prev = L.fills[-1].qty
eq("4차 누적 = 1차의 15배", round(L.total_qty / base, 6), 15.0)
eq("거래소 수량 = 내부 수량", round(ex.pos["LONG"]["qty"], 10), round(L.total_qty, 10))
for _ in range(3):
    t = L.avg_price * (1 - core.STEP_TRIGGER_PCT - 0.01)
    L.on_tick(t, 50, (t, t * 1.1, t * 0.9), CLOCK.time())
check("4차 초과 진입 없음", L.step <= 4, f"step={L.step}")

# ══════════════════════════════════════════════════════════
section("4. 익절 / 손절 / 쿨다운 / 회로차단기")

b, ex = bot_with()
L = b.long
L._enter_initial(60000.0)
tp = L.avg_price * (1 + core.TP_PCT)
L.on_tick(tp * 0.99999, 50, (tp, tp * 1.1, tp * 0.9), CLOCK.time())
check("익절 미달 유지", L.in_position)
L.on_tick(tp * 1.00001, 50, (tp, tp * 1.1, tp * 0.9), CLOCK.time())
eq("익절 후 step0", L.step, 0)
eq("익절 후 거래소 정리", ex.pos["LONG"]["qty"], 0.0)
eq("쿨다운 = 현재+180", round(L.cooldown_until - CLOCK.time(), 3), 180.0)
L.on_tick(50000.0, 10.0, (50000, 55000, 49000), CLOCK.time())
eq("쿨다운 중 미진입", L.step, 0)
CLOCK.advance(181)
L.on_tick(50000.0, 10.0, (50000, 55000, 49000), CLOCK.time())
eq("쿨다운 후 재진입", L.step, 1)

b, ex = bot_with()
L = b.long
L._enter_initial(60000.0)
bal0 = ex.balance
sl = L.avg_price * (1 - core.STOP_LOSS_PCT - 1e-9)
L.on_tick(sl, 50, (sl, sl * 1.1, sl * 0.9), CLOCK.time())
eq("손절 후 step0", L.step, 0)
check("손절로 잔고 감소", ex.balance < bal0, f"{bal0:.2f}→{ex.balance:.2f}")

b, ex = bot_with()
L = b.long
for _ in range(3):
    CLOCK.advance(200)
    L.on_tick(60000.0, 10.0, (60000, 61000, 59000), CLOCK.time())
    if not L.in_position:
        break
    s = L.avg_price * (1 - core.STOP_LOSS_PCT - 0.001)
    L.on_tick(s, 50, (s, s * 1.1, s * 0.9), CLOCK.time())
eq("연속손절 3회", L.consecutive_sl, 3)
check("3회 후 자동정지", L.halted)
CLOCK.advance(200)
L.on_tick(60000.0, 10.0, (60000, 61000, 59000), CLOCK.time())
eq("정지 후 미진입", L.step, 0)

b, ex = bot_with()
L = b.long
L._enter_initial(60000.0)
s = L.avg_price * (1 - core.STOP_LOSS_PCT - 0.001)
L.on_tick(s, 50, (s, s * 1.1, s * 0.9), CLOCK.time())
CLOCK.advance(200)
L._enter_initial(60000.0)
t2 = L.avg_price * (1 + core.TP_PCT + 0.001)
L.on_tick(t2, 50, (t2, t2 * 1.1, t2 * 0.9), CLOCK.time())
eq("익절 시 연속손절 리셋", L.consecutive_sl, 0)

# ══════════════════════════════════════════════════════════
section("5. 롱/숏 독립성 · 손익 부호")

b, ex = bot_with()
L, S = b.long, b.short
L._enter_initial(60000.0)
S._enter_initial(60000.0)
check("양방향 동시 보유", L.in_position and S.in_position)
t = L.avg_price * (1 + core.TP_PCT + 0.001)
L.on_tick(t, 50, (t, t * 1.1, t * 0.9), CLOCK.time())
check("롱만 익절, 숏 유지", L.step == 0 and S.step == 1)
eq("숏 거래소 수량 유지", round(ex.pos["SHORT"]["qty"], 10), round(S.total_qty, 10))
tr = S.avg_price * (1 + core.STEP_TRIGGER_PCT + 1e-9)
S.on_tick(tr, 50, (tr, tr * 1.1, tr * 0.9), CLOCK.time())
eq("숏은 상승 시 물타기", S.step, 2)
check("숏 손익: 하락=이익", S._realized_pnl(S.avg_price * 0.99) > 0)
check("숏 손익: 상승=손실", S._realized_pnl(S.avg_price * 1.01) < 0)
b2, ex2 = bot_with(Exchange(fee=0.0))
L2 = b2.long
L2._enter_initial(60000.0)
q = L2.total_qty
eq("롱 +1% 손익", round(L2._realized_pnl(60600.0), 6), round(q * 600.0, 6))
eq("평단가에서 손익0", round(L2._realized_pnl(L2.avg_price), 9), 0.0)

# ══════════════════════════════════════════════════════════
section("6. 이상 입력 방어")

ex = Exchange()
ex.fail_balance = True
b, _ = bot_with(ex)
b.long.on_tick(60000.0, 10.0, (60000, 61000, 59000), CLOCK.time())
eq("잔고 못읽으면 미진입", b.long.step, 0)
eq("주문도 안 나감", len(ex.orders), 0)
ex.fail_balance = False
b.long.on_tick(60000.0, 10.0, (60000, 61000, 59000), CLOCK.time())
eq("잔고 복구 시 진입", b.long.step, 1)

b, _ = bot_with()
ok = True
err = None
try:
    for bad in (0.0, -1.0, float("nan"), float("inf"), None):
        b.long.on_tick(bad, 10.0, (0, 0, 0), CLOCK.time())
        b.short.on_tick(bad, 90.0, (0, 0, 0), CLOCK.time())
except Exception as e:
    ok, err = False, e
check("가격 0/음수/NaN/inf/None 안전", ok, "" if ok else f"{type(err).__name__}: {err}")
check("비정상 가격 미진입", not b.long.in_position and not b.short.in_position)

b, _ = bot_with()
b.long._enter_initial(60000.0)
held = (b.long.step, b.long.total_qty)
ok = True
try:
    for bad in (0.0, -1.0, float("nan"), None):
        b.long.on_tick(bad, 50.0, (0, 0, 0), CLOCK.time())
except Exception:
    ok = False
check("보유 중 비정상 가격 안전", ok)
check("보유 중 잘못 청산 안 됨", (b.long.step, b.long.total_qty) == held)

b, _ = bot_with()
ok = True
try:
    b.long.on_tick(60000.0, None, None, CLOCK.time())
    b.short.on_tick(60000.0, None, (1, 2, 3), CLOCK.time())
    b.long.on_tick(60000.0, 10.0, None, CLOCK.time())
except Exception:
    ok = False
check("지표 None 안전", ok)
check("지표 None 미진입", not b.long.in_position and not b.short.in_position)


class Reject(Exchange):
    def fill_order(self, *a):
        raise RuntimeError("거래소 거부")


b, _ = bot_with(Reject())
try:
    b.long.on_tick(60000.0, 10.0, (60000, 61000, 59000), CLOCK.time())
except Exception:
    pass
check("주문 거부 시 내부상태 안 바뀜", not b.long.in_position)

# ══════════════════════════════════════════════════════════
section("7. 계약 단위 보정")

for cs in (0.0001, 0.001, 1.0):
    ex = Exchange(contract_size=cs)
    b, _ = bot_with(ex)
    L = b.long
    L._enter_initial(60000.0)
    if L.total_qty > 0:
        r = L.total_qty / cs
        check(f"계약{cs}: 진입수량 배수", abs(r - round(r)) < 1e-6, f"{r:.4f}계약")
        t = L.avg_price * (1 - core.STEP_TRIGGER_PCT - 1e-9)
        L.on_tick(t, 50, (t, t * 1.1, t * 0.9), CLOCK.time())
        r2 = L.total_qty / cs
        check(f"계약{cs}: 물타기 후 배수", abs(r2 - round(r2)) < 1e-6)
    else:
        check(f"계약{cs}: 최소미달 미진입", L.step == 0)

# ══════════════════════════════════════════════════════════
section("8. 단계별 예상 금액 (martingale_ladder)")

fills = [Fill(63785.30, 0.0257)]
lad = martingale_ladder(Side.LONG, fills)
eq("1~4단계 반환", len(lad), 4)
check("1단계만 체결표시", [x["done"] for x in lad] == [True, False, False, False])
sim = [Fill(63785.30, 0.0257)]
for w in lad[1:]:
    tq = sum(f.qty for f in sim)
    avg = sum(f.price * f.qty for f in sim) / tq
    trig = avg * (1 - core.STEP_TRIGGER_PCT)
    eq(f"{w['stage']}차 예상가=실제트리거", round(w["price"], 6), round(trig, 6))
    eq(f"{w['stage']}차 금액=가격×수량", round(w["usdt"], 6), round(trig * sim[-1].qty * 2, 6))
    sim.append(Fill(trig, sim[-1].qty * 2))
ls = martingale_ladder(Side.SHORT, [Fill(63818.0, 0.0258)])
check("숏은 가격 상승", all(ls[i]["price"] < ls[i + 1]["price"] for i in range(3)))
check("롱은 가격 하락", all(lad[i]["price"] > lad[i + 1]["price"] for i in range(3)))
eq("빈 체결내역", martingale_ladder(Side.LONG, []), [])

# ══════════════════════════════════════════════════════════
section("9. 텔레그램 다중 전송 / 잔고 숨김")

eq("쉼표", parse_chat_ids("1,2"), ["1", "2"])
eq("공백", parse_chat_ids("1 2"), ["1", "2"])
eq("혼합+중복제거", parse_chat_ids(" 1 , 2 ,,1\n3 "), ["1", "2", "3"])
eq("채널명 유지", parse_chat_ids("@ch,-100123"), ["@ch", "-100123"])
eq("빈문자열", parse_chat_ids(""), [])
eq("None", parse_chat_ids(None), [])

sent = []


class FR:
    def __init__(self, b):
        self._b = b

    def json(self):
        return self._b


class FakeReq:
    @staticmethod
    def post(url, data=None, timeout=None):
        sent.append((data["chat_id"], data["text"]))
        if data["chat_id"] == "BAD":
            return FR({"ok": False, "error_code": 400, "description": "chat not found"})
        if data["chat_id"] == "BOOM":
            raise RuntimeError("net")
        return FR({"ok": True})


orig = core.requests
core.requests = FakeReq
n = TelegramNotifier("T", "A, BAD, BOOM, B")
n.send("x")
eq("실패 있어도 전 대상 시도", [c for c, _ in sent], ["A", "BAD", "BOOM", "B"])
sent.clear()
TelegramNotifier("", "A").send("z")
eq("토큰 없으면 미전송", sent, [])
TelegramNotifier("T", "").send("z")
eq("ChatID 없으면 미전송", sent, [])
sent.clear()
core.TELEGRAM_SHOW_BALANCE = False
n2 = TelegramNotifier("T", "A")
n2.send("본문 | 잔고 1,600.00 USDT", "본문")
check("잔고 숨김 시 텔레그램에 잔고 없음", "잔고" not in sent[0][1], sent[0][1])
core.requests = orig
defaults(core)

print()
sys.exit(0 if report("검증 1 — 엔진 핵심") else 1)
