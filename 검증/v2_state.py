# -*- coding: utf-8 -*-
"""최종 검증 2 — 상태 저장 / 재시작 / 헷지 / 증거금·오류 처리."""
import sys, os, math, json, logging, threading
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from v_common import *

import hedged_martingale_bot as core
from hedged_martingale_bot import HedgedMartingaleBot, Side, Fill, LiveBroker

core.logger.setLevel(logging.CRITICAL)
CLOCK = Clock()
core.time = CLOCK


def mk(ex=None, sp=None, **kw):
    defaults(core)
    for k, v in kw.items():
        setattr(core, k, v)
    ex = ex or Exchange()
    return HedgedMartingaleBot(ex, Notifier(), "V", state_path=sp), ex


def to_stage(m, n, p0=65000.0):
    m._enter_initial(p0)
    for _ in range(n - 1):
        t = m.avg_price * (1 - core.STEP_TRIGGER_PCT - 1e-9)
        m.on_tick(t, 50, (t, t * 1.1, t * 0.9), CLOCK.time())


# ══════════════════════════════════════════════════════════
section("10. 상태 저장 / 복원 / 손상 대응")

sp = fresh_path()
ex = Exchange()
b, _ = mk(ex, sp)
to_stage(b.long, 2)
b.short._enter_initial(65000.0)
b._save_state()
snap = (b.long.step, b.long.total_qty, b.long.avg_price, b.short.step)
check("상태 파일 생성", os.path.exists(sp))

b2, _ = mk(ex, sp)
check("재시작 상태 일치", (b2.long.step, b2.long.total_qty, b2.long.avg_price, b2.short.step) == snap,
      f"{(b2.long.step, b2.long.total_qty)}")

b2._save_state()
check("2회 저장 시 .bak 생성", os.path.exists(sp + ".bak"))
check("임시파일 안 남음", not os.path.exists(sp + ".tmp"))
open(sp, "w").write("### 깨짐")
b3, _ = mk(ex, sp)
eq("본파일 손상 → .bak 복구", b3.long.step, snap[0])
open(sp, "w").write("###")
open(sp + ".bak", "w").write("###")
b4, _ = mk(ex, sp)
check("둘 다 손상 → 거래소로 복구", b4.long.in_position and abs(b4.long.total_qty - snap[1]) < 1e-9)

os.remove(sp)
b5, _ = mk(ex, sp)
eq("상태 유실 → 거래소 복구(단계)", b5.long.step >= snap[0], True)
eq("상태 유실 → 수량 정확", round(b5.long.total_qty, 10), round(snap[1], 10))

ex.pos["LONG"] = {"qty": 0.0, "cost": 0.0}
ex.pos["SHORT"] = {"qty": 0.0, "cost": 0.0}
b6, _ = mk(ex, sp)
check("거래소 flat → 내부도 flat", not b6.long.in_position and not b6.short.in_position)


class NoFetch(Exchange):
    def fetch_position(self, side):
        return None


sp2 = fresh_path()
exA = Exchange()
bA, _ = mk(exA, sp2)
bA.long._enter_initial(65000.0)
bA._save_state()
exB = NoFetch()
exB.balance = exA.balance
bB, _ = mk(exB, sp2)
eq("조회 실패 시 저장상태 유지", bB.long.step, 1)

b7, _ = mk(Exchange(), "/proc/불가/state.json")
ok = True
try:
    b7.long._enter_initial(65000.0)
    b7._save_state()
except Exception:
    ok = False
check("저장 실패해도 매매 계속", ok and b7.long.in_position)

# ══════════════════════════════════════════════════════════
section("11. 재시작 단계 추정 (안전 방향)")

for stage in (1, 2, 3, 4):
    for mult, label in ((0.4, "잔고 -60%"), (0.7, "잔고 -30%"), (1.0, "잔고 동일"), (1.5, "잔고 +50%")):
        ex = Exchange()
        b, _ = mk(ex)
        to_stage(b.long, stage)
        real = b.long.step
        ex.balance = 1600.0 * mult
        b2, _ = mk(ex)
        got = b2.long.step
        tag = "정확" if got == real else ("과대(안전)" if got > real else "과소(위험)")
        check(f"{stage}차 · {label} → {got}차 [{tag}]", got >= real, f"실제 {real}")

print()
for stage in (1, 2, 3, 4):
    ex = Exchange()
    b, _ = mk(ex)
    to_stage(b.long, stage)
    real = b.long.step
    used = b.long.total_qty * b.long.avg_price / core.LEVERAGE
    ex.balance = max(1.0, 1600.0 - used)
    b2, _ = mk(ex)
    check(f"[현실] {stage}차 · 증거금 묶임(free {ex.balance:.0f}) → {b2.long.step}차",
          b2.long.step >= real, f"실제 {real}")

# ══════════════════════════════════════════════════════════
section("12. 재시작 후 매매 지속")

for kill in (1, 2, 3):
    sp = fresh_path()
    ex = Exchange()
    b, _ = mk(ex, sp)
    to_stage(b.long, kill)
    b._save_state()
    before = (b.long.step, round(b.long.total_qty, 10), round(b.long.avg_price, 4))
    b2, _ = mk(ex, sp)
    L = b2.long
    check(f"{kill}차 종료→재시작 일치",
          (L.step, round(L.total_qty, 10), round(L.avg_price, 4)) == before)
    t = L.avg_price * (1 - core.STEP_TRIGGER_PCT - 1e-9)
    L.on_tick(t, 50, (t, t * 1.1, t * 0.9), CLOCK.time())
    eq(f"{kill}차 재시작 후 물타기", L.step, kill + 1)
    tp = L.avg_price * (1 + core.TP_PCT + 1e-9)
    L.on_tick(tp, 50, (tp, tp * 1.1, tp * 0.9), CLOCK.time())
    eq(f"{kill}차 재시작 후 익절", L.step, 0)
    eq(f"{kill}차 거래소 정리", ex.pos["LONG"]["qty"], 0.0)

# ══════════════════════════════════════════════════════════
section("13. 3차 헷지")

b, ex = mk(HEDGE_AT_STEP=3)
L = b.long
to_stage(L, 3)
hp = L.fills[-1].price
b._handle_hedges(hp, CLOCK.time())
h = b.hedges.get(Side.LONG)
check("3차 도달 시 헷지 진입", h is not None)
eq("헷지 수량 = 롱 수량", round(h.qty, 12), round(L.total_qty, 12))
eq("헷지 진입가 = 전달 현재가", round(h.price, 6), round(hp, 6))
check("거래소 반대 포지션 생성", ex.pos["SHORT"]["qty"] > 0)
vals = {round(L._realized_pnl(L.avg_price * m) + b._hedge_pnl(Side.LONG, L.avg_price * m), 6)
        for m in (0.85, 0.9, 0.95, 1.0, 1.05, 1.2, 1.5)}
check("모든 가격에서 손익 고정", len(vals) == 1, f"{list(vals)[0]:+.2f} USDT")

before = (L.step, L.total_qty)
frozen = b._handle_hedges(L.avg_price * 0.95, CLOCK.time())
check("헷지된 방향 frozen", Side.LONG in frozen)
b.on_price(L.avg_price * 0.95, [L.avg_price] * 100, CLOCK.time())
check("헷지 중 물타기 안 함", (L.step, L.total_qty) == before)
check("헷지 중 손절 안 함", L.in_position)
check("반대편은 계속 동작 가능", Side.SHORT not in frozen)

b.on_price(L.avg_price * 1.0001, [L.avg_price] * 100, CLOCK.time() + 500)
check("평단 복귀 시 해제", Side.LONG not in b.hedges)
check("롱 정리됨", not L.in_position)
eq("거래소 롱 정리", ex.pos.get("LONG", {"qty": 0})["qty"], 0.0)
eq("거래소 헷지 정리", ex.pos.get("SHORT", {"qty": 0})["qty"], 0.0)

b, ex = mk(HEDGE_AT_STEP=3)
b.short._enter_initial(65000.0)
sq = b.short.total_qty
to_stage(b.long, 3, 64000.0)
b._handle_hedges(b.long.fills[-1].price, CLOCK.time())
check("반대편 보유 중에도 헷지", Side.LONG in b.hedges)
hq = b.hedges[Side.LONG].qty
eq("거래소 숏 = 숏모듈+헷지", round(ex.pos["SHORT"]["qty"], 9), round(sq + hq, 9))
eq("숏모듈은 자기 수량만 인식", round(b.short.total_qty, 12), round(sq, 12))
tp = b.short.avg_price * (1 - core.TP_PCT - 1e-9)
b.short.on_tick(tp, 50, (tp, tp * 1.1, tp * 0.9), CLOCK.time())
check("숏모듈 익절됨", not b.short.in_position)
eq("헷지 물량 그대로 남음", round(ex.pos["SHORT"]["qty"], 9), round(hq, 9))
check("헷지 기록 유지", Side.LONG in b.hedges)

sp = fresh_path()
b, ex = mk(sp=sp, HEDGE_AT_STEP=3)
to_stage(b.long, 3)
b._handle_hedges(b.long.fills[-1].price, CLOCK.time())
b._save_state()
hs = (b.long.step, round(b.long.total_qty, 10),
      round(b.hedges[Side.LONG].qty, 10), round(b.hedges[Side.LONG].price, 4))
b2, _ = mk(ex, sp, HEDGE_AT_STEP=3)
h2 = b2.hedges.get(Side.LONG)
check("재시작 시 헷지 복원", h2 is not None)
if h2:
    check("헷지 복원값 일치",
          (b2.long.step, round(b2.long.total_qty, 10), round(h2.qty, 10), round(h2.price, 4)) == hs,
          f"{(b2.long.step, b2.long.total_qty)}")
    eq("헷지 물량을 롱에 잘못 더하지 않음", round(b2.long.total_qty, 9), hs[1])

b, ex = mk(HEDGE_AT_STEP=3)
to_stage(b.long, 3)
NOW = 61000.0
b._handle_hedges(NOW, CLOCK.time())
eq("다른 시세에서 걸려도 현재가 기록", round(b.hedges[Side.LONG].price, 6), NOW)
lock = b.long._realized_pnl(NOW) + b._hedge_pnl(Side.LONG, NOW)
eq("고정손익 = 그 시점 평가손실", round(lock, 6), round(b.long._realized_pnl(NOW), 6))

b, ex = mk(HEDGE_AT_STEP=0)
to_stage(b.long, 4)
eq("헷지 OFF면 4차까지", b.long.step, 4)
check("헷지 OFF면 헷지 없음", not b.hedges)

# ══════════════════════════════════════════════════════════
section("14. 거래소 오류 안내")

IP = 'gate {"message":"Request IP not in whitelist: 34.47.67.181","label":"FORBIDDEN"}'
KEY = 'gate {"message":"Invalid key provided","label":"INVALID_KEY"}'
PERM = 'gate {"label":"FORBIDDEN","message":"no permission for futures"}'
for err, kind, label in ((IP, "recoverable", "IP 화이트리스트"),
                         (KEY, "fatal", "잘못된 키"),
                         (PERM, "fatal", "선물 권한 없음"),
                         ("Connection timeout", None, "일시적 오류")):
    r = LiveBroker.classify_api_error(Exception(err))
    got = r[0] if r else None
    check(f"{label} → {kind or '분류 안 함'}", got == kind, str(got))
h = LiveBroker.explain_api_error(Exception(IP))
check("IP 오류에 차단된 IP 표시", h and "34.47.67.181" in h)
check("IP 오류에 조치방법 안내", h and "허용 목록" in h)

# ══════════════════════════════════════════════════════════
section("15. 스레드 안전성 (동시 진입/청산)")

ex = Exchange()
b, _ = mk(ex)
errors = []


def worker():
    try:
        for i in range(300):
            b.on_price(60000.0 + (i % 7) * 10, [60000.0] * 100, CLOCK.time() + i)
    except Exception as e:
        errors.append(e)


ths = [threading.Thread(target=worker) for _ in range(3)]
for t in ths:
    t.start()
for t in ths:
    t.join()
check("동시 호출 예외 없음", not errors, str(errors[:1]))
for m, k in ((b.long, "LONG"), (b.short, "SHORT")):
    exq = ex.pos.get(k, {"qty": 0.0})["qty"]
    check(f"{k} 내부수량=거래소수량", abs(exq - m.total_qty) < 1e-6,
          f"{m.total_qty:.8f} vs {exq:.8f}")

print()
sys.exit(0 if report("검증 2 — 상태·재시작·헷지") else 1)
