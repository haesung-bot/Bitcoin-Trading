# -*- coding: utf-8 -*-
"""검증 13 — 야간 정지 시간대.

정지 시간대(예: 한국시간 21시~1시)에 기대하는 동작:
  · 신규 진입(1차)  안 함
  · 물타기(다음 차수) 안 함
  · 하드손절        안 함  ← 이 시간에 손절이 터지는 것을 막는 것이 목적
  · 익절            함     ← 수익 구간이 오면 정리하고 나온다
  · 헷지 신규       안 함 / 헷지 해제는 함
그리고 시간이 끝나면 그 자리에서 물타기·손절 판단을 다시 이어간다.
"""
import sys, os, calendar, logging
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from v_common import *
import hedged_martingale_bot as core
from hedged_martingale_bot import HedgedMartingaleBot, Side, Fill

CLOCK = Clock()
TZ = 9


def kst(hour, minute=0, day=4):
    """한국시각 hh:mm 에 해당하는 UTC epoch."""
    return calendar.timegm((2026, 9, day, hour - TZ, minute, 0, 0, 0, 0))


def setup(lev=25, pos=0.03, steps=3, start=21, end=1):
    defaults(core, lev=lev, pos=pos)
    core.MAX_STEPS = steps
    core.HEDGE_AT_STEP = 0
    core.MAX_CONSECUTIVE_SL = 10 ** 9
    core.QUIET_TZ_OFFSET = TZ
    core.QUIET_START_HOUR, core.QUIET_END_HOUR = start, end


def make_bot(balance=1600.0):
    ex = Exchange(balance=balance, fee=0.0005)
    bot = HedgedMartingaleBot(ex, Notifier(), "QUIET", state_path=fresh_path())
    return ex, bot


# 진입 신호가 확실히 나오도록 만든 창(롱: RSI 낮고 하단밴드 이탈)
def falling_window(last):
    return [last * (1 + 0.02 * (1 - i / 99.0)) for i in range(99)] + [last]


section("A. 시간 판정")
setup()
for h, want in ((20, False), (21, True), (23, True), (0, True), (1, False), (12, False)):
    check(f"한국시간 {h:02d}시 → {'정지' if want else '정상'}",
          core.in_quiet_hours(kst(h)) == want, f"got={core.in_quiet_hours(kst(h))}")
setup(start=2, end=5)
for h, want in ((1, False), (3, True), (5, False)):
    check(f"자정 안 넘는 구간 {h:02d}시 → {'정지' if want else '정상'}",
          core.in_quiet_hours(kst(h)) == want)
setup(start=-1, end=-1)
check("꺼두면 항상 정상", not core.quiet_hours_enabled() and not core.in_quiet_hours(kst(22)))
setup(start=3, end=3)
check("시작=종료면 꺼진 것으로 본다", not core.quiet_hours_enabled())

section("B. 정지 시간대에는 신규 진입을 하지 않는다")
setup()
ex, bot = make_bot()
p = 65000.0
w = falling_window(p)
bot.on_price(p, w, kst(22))
check("22시: 롱 신규 진입 없음", not bot.long.in_position)
check("22시: 숏 신규 진입 없음", not bot.short.in_position)
check("22시: 거래소에 주문 안 나감", not ex.pos.get("LONG", {}).get("qty"))

ex, bot = make_bot()
bot.on_price(p, w, kst(14))
check("14시(정상시간): 롱 진입함", bot.long.in_position, f"step={bot.long.step}")

section("C. 정지 시간대에는 물타기와 손절을 하지 않는다")
setup()
ex, bot = make_bot()
bot.long._enter_initial(p)
step0, qty0 = bot.long.step, bot.long.total_qty

# 물타기 트리거(-1.2%)를 훨씬 넘겨도 단계가 그대로여야 한다
low = p * (1 - core.STEP_TRIGGER_PCT * 1.5)
bot.on_price(low, falling_window(low), kst(22))
check("22시: 물타기 안 함", bot.long.step == step0, f"{step0}차 → {bot.long.step}차")
check("22시: 수량 그대로", abs(bot.long.total_qty - qty0) < 1e-12)

# 하드손절 트리거(-2.5%)를 넘겨도 버텨야 한다
deep = p * (1 - core.STOP_LOSS_PCT * 1.6)
bot.on_price(deep, falling_window(deep), kst(23, 30))
check("23시30분: 하드손절 안 함(버팀)", bot.long.in_position,
      f"손실 {bot.long._pnl_pct(deep)*100:.2f}%")
check("23시30분: 단계도 그대로", bot.long.step == step0)

section("D. 정지 시간대에도 익절은 한다")
setup()
ex, bot = make_bot()
bot.long._enter_initial(p)
up = p * (1 + core.TP_PCT * 1.2)
before = ex.balance
bot.on_price(up, falling_window(up), kst(23))
check("23시: 수익 구간에서 익절함", not bot.long.in_position)
check("23시: 잔고가 늘었다", ex.balance > before, f"${before:,.2f} → ${ex.balance:,.2f}")

# 익절 직후 같은 시간대에 다시 들어가지는 않아야 한다
bot.on_price(p, falling_window(p), kst(23, 30))
check("익절 후 재진입 안 함", not bot.long.in_position)

section("E. 시간이 끝나면 이어서 판단한다")
setup()
ex, bot = make_bot()
bot.long._enter_initial(p)
step0 = bot.long.step
bot.on_price(low, falling_window(low), kst(22))          # 정지 중 — 물타기 없음
check("정지 중에는 그대로", bot.long.step == step0)
bot.on_price(low, falling_window(low), kst(1, 1))        # 1시 이후 — 물타기 재개
check("1시 이후: 다음 차수 진입함", bot.long.step == step0 + 1,
      f"{step0}차 → {bot.long.step}차")

ex, bot = make_bot()
bot.long._enter_initial(p)
bot.on_price(deep, falling_window(deep), kst(23))         # 정지 중 — 손절 없음
check("정지 중에는 손절 안 함", bot.long.in_position)
bot.on_price(deep, falling_window(deep), kst(2))          # 2시 — 손절 실행
check("시간 끝나면 손절함", not bot.long.in_position)

ex, bot = make_bot()
bot.on_price(p, falling_window(p), kst(23))
check("정지 중 신규 진입 없음", not bot.long.in_position)
bot.on_price(p, falling_window(p), kst(1, 30))
check("1시30분: 신규 진입 재개", bot.long.in_position)

section("F. 안내 메시지")
setup()


class Cap:
    def __init__(self):
        self.msgs = []

    def send(self, text, telegram_text=None):
        self.msgs.append(text)


ex = Exchange(balance=1600.0, fee=0.0005)
cap = Cap()
bot = HedgedMartingaleBot(ex, cap, "QUIET", state_path=fresh_path())
bot.on_price(p, falling_window(p), kst(20))               # 켜자마자 정상시간 → 안내가 나가면 안 된다
check("켤 때 정상시간이면 정지 안내 없음",
      not [m for m in cap.msgs if "🌙" in m or "☀" in m], str(cap.msgs[:1]))
bot.on_price(p, falling_window(p), kst(21, 5))            # 정상 → 정지 : 안내 1회
enter = [m for m in cap.msgs if "🌙" in m]
check("정지 시작 안내 나감", len(enter) == 1, f"{len(enter)}건")
check("안내에 시간대 표시", enter and "21:00~01:00" in enter[0], enter[0][:60] if enter else "")
check("안내에 무엇을 멈추는지 설명", enter and "신규 진입" in enter[0] and "손절" in enter[0])

bot.on_price(p, falling_window(p), kst(22))               # 계속 정지 → 중복 안내 없음
bot.on_price(p, falling_window(p), kst(23))
check("정지 중 반복 안내 없음", len([m for m in cap.msgs if "🌙" in m]) == 1)

bot.on_price(p, falling_window(p), kst(1, 10))            # 정지 → 정상 : 해제 안내
leave = [m for m in cap.msgs if "☀" in m]
check("정지 해제 안내 나감", len(leave) == 1, f"{len(leave)}건")
check("해제 안내에 재개 표시", leave and "재개" in leave[0])
bot.on_price(p, falling_window(p), kst(2))
check("해제 후 반복 안내 없음", len([m for m in cap.msgs if "☀" in m]) == 1)

cap2 = Cap()
ex2 = Exchange(balance=1600.0, fee=0.0005)
bot2 = HedgedMartingaleBot(ex2, cap2, "QUIET", state_path=fresh_path())
bot2.on_price(p, falling_window(p), kst(23))              # 정지 한가운데에서 켠 경우
check("정지 중에 켜면 바로 안내", len([m for m in cap2.msgs if "🌙" in m]) == 1,
      str(cap2.msgs[:1]))

section("G. 하트비트 표시")
setup()
buf = []


class H(logging.Handler):
    def emit(self, r):
        buf.append(self.format(r))


h = H()
h.setFormatter(logging.Formatter("%(message)s"))
old = core.logger.handlers
core.logger.handlers = [h]
core.logger.setLevel(logging.INFO)
ex, bot = make_bot()
import time as _t
_real = _t.time
_t.time = lambda: kst(22)
try:
    bot._log_heartbeat(p)
finally:
    _t.time = _real
    core.logger.handlers = old
check("정지 중 하트비트에 표시됨", any("정지 시간대" in m for m in buf), buf[-1] if buf else "")

buf.clear()
core.logger.handlers = [h]
_t.time = lambda: kst(14)
try:
    bot._log_heartbeat(p)
finally:
    _t.time = _real
    core.logger.handlers = old
check("정상 시간엔 표시 없음", not any("정지 시간대" in m for m in buf), buf[-1] if buf else "")

section("H. 헷지는 새로 걸지 않고, 걸린 것은 풀 수 있다")
setup()
core.HEDGE_AT_STEP = 3
ex, bot = make_bot()
bot.long.fills = [Fill(p, 0.01), Fill(p * 0.988, 0.02), Fill(p * 0.976, 0.04)]
bot.long._recalc_avg()
bot.long.step = 3
ex.pos["LONG"] = {"qty": bot.long.total_qty, "entry": bot.long.avg_price}
bot._handle_hedges(p * 0.95, kst(22))
check("정지 중 헷지 새로 안 걸림", Side.LONG not in bot.hedges)
bot._handle_hedges(p * 0.95, kst(14))
check("정상 시간엔 헷지 걸림", Side.LONG in bot.hedges)
bot._handle_hedges(bot.long.avg_price, kst(23))
check("정지 중에도 헷지 해제는 됨", Side.LONG not in bot.hedges)
core.HEDGE_AT_STEP = 0

section("I. 기능을 끄면 예전과 똑같다")
setup(start=-1, end=-1)
ex, bot = make_bot()
bot.on_price(p, falling_window(p), kst(22))
check("꺼두면 22시에도 진입함", bot.long.in_position)
bot.long._enter_initial  # noqa
ex, bot = make_bot()
bot.long._enter_initial(p)
bot.on_price(deep, falling_window(deep), kst(22))
check("꺼두면 22시에도 손절함", not bot.long.in_position)

print()
sys.exit(0 if report("검증 13 — 야간 정지 시간대") else 1)
