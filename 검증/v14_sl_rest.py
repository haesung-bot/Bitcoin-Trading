# -*- coding: utf-8 -*-
"""검증 14 — 손절 후 휴식.

하드손절이 나면 그 방향은 STOP_LOSS_COOLDOWN_SEC(기본 1시간) 동안 새로 들어가지 않는다.
익절은 기존 짧은 쿨다운(COOLDOWN_SEC) 그대로다. 반대 방향은 영향을 받지 않는다.
"""
import sys, os, calendar
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from v_common import *
import hedged_martingale_bot as core
from hedged_martingale_bot import HedgedMartingaleBot, Side, Fill

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T0 = 1_800_000_000.0        # 정지 시간대와 무관한 시각을 쓴다


def setup(rest=3600):
    defaults(core, lev=25, pos=0.03)
    core.MAX_STEPS = 3
    core.HEDGE_AT_STEP = 0
    core.MAX_CONSECUTIVE_SL = 10 ** 9
    core.QUIET_START_HOUR = core.QUIET_END_HOUR = -1
    core.QUIET_STOP_LOSS_STEP = 0
    core.STOP_LOSS_COOLDOWN_SEC = rest


def make_bot(balance=1600.0):
    ex = Exchange(balance=balance, fee=0.0005)
    return ex, HedgedMartingaleBot(ex, Notifier(), "REST", state_path=fresh_path())


def falling(last):
    return [last * (1 + 0.02 * (1 - i / 99.0)) for i in range(99)] + [last]


def rising(last):
    return [last * (1 - 0.02 * (1 - i / 99.0)) for i in range(99)] + [last]


p = 65000.0

section("A. 손절이 나면 1시간 쉰다")
setup()
ex, bot = make_bot()
bot.long._enter_initial(p)
deep = p * (1 - core.STOP_LOSS_PCT * 1.6)
bot.long._stop_loss(deep, T0)
check("손절로 포지션 정리됨", not bot.long.in_position)
check("쿨다운이 걸림", bot.long.cooldown_until is not None)
eq("정확히 1시간", round(bot.long.cooldown_until - T0), 3600)

check("30분 뒤에도 아직 쉬는 중", bot.long._in_cooldown(T0 + 1800))
check("59분 뒤에도 아직 쉬는 중", bot.long._in_cooldown(T0 + 3540))
check("1시간 1초 뒤에는 풀림", not bot.long._in_cooldown(T0 + 3601))

section("B. 쉬는 동안에는 진입 신호가 와도 안 들어간다")
setup()
ex, bot = make_bot()
bot.long._enter_initial(p)
bot.long._stop_loss(deep, T0)
bot.on_price(p, falling(p), T0 + 600)
check("10분 뒤: 롱 진입 안 함", not bot.long.in_position)
bot.on_price(p, falling(p), T0 + 3000)
check("50분 뒤: 롱 진입 안 함", not bot.long.in_position)
bot.on_price(p, falling(p), T0 + 3700)
check("1시간 뒤: 롱 진입함", bot.long.in_position)

section("C. 반대 방향은 그대로 매매한다")
setup()
ex, bot = make_bot()
bot.long._enter_initial(p)
bot.long._stop_loss(deep, T0)
check("숏은 쿨다운 없음", not bot.short._in_cooldown(T0 + 60))
bot.on_price(p, rising(p), T0 + 600)
check("10분 뒤: 숏은 진입함", bot.short.in_position)
check("10분 뒤: 롱은 여전히 대기", not bot.long.in_position)

section("D. 익절은 예전대로 짧게 쉰다")
setup()
ex, bot = make_bot()
bot.long._enter_initial(p)
up = p * (1 + core.TP_PCT * 1.2)
bot.long._take_profit(up, T0)
eq("익절 쿨다운은 기본값", round(bot.long.cooldown_until - T0), core.COOLDOWN_SEC)
check("익절 뒤엔 3분만 지나면 풀림", not bot.long._in_cooldown(T0 + core.COOLDOWN_SEC + 1))

section("E. 껐다 켜도 쉬는 시간이 유지된다")
setup()
ex = Exchange(balance=1600.0, fee=0.0005)
sp = fresh_path()
bot = HedgedMartingaleBot(ex, Notifier(), "REST", state_path=sp)
bot.long._enter_initial(p)
bot.long._stop_loss(deep, T0)
bot._save_state()
until = bot.long.cooldown_until

bot2 = HedgedMartingaleBot(ex, Notifier(), "REST", state_path=sp)
eq("재시작 후에도 같은 시각까지", bot2.long.cooldown_until, until)
check("재시작 직후에도 진입 안 함", bot2.long._in_cooldown(T0 + 600))
bot2.on_price(p, falling(p), T0 + 600)
check("재시작 후 10분: 진입 안 함", not bot2.long.in_position)

section("F. 안내 메시지")
setup()


class Cap:
    def __init__(self):
        self.msgs = []

    def send(self, text, telegram_text=None):
        self.msgs.append(text)


ex = Exchange(balance=1600.0, fee=0.0005)
cap = Cap()
bot = HedgedMartingaleBot(ex, cap, "REST", state_path=fresh_path())
bot.long._enter_initial(p)
cap.msgs.clear()
bot.long._stop_loss(deep, T0)
rest_msgs = [m for m in cap.msgs if "⏸" in m]
check("휴식 안내 나감", len(rest_msgs) == 1, str(cap.msgs))
check("안내에 '1시간' 표시", rest_msgs and "1시간" in rest_msgs[0], rest_msgs[0] if rest_msgs else "")
check("안내에 방향 표시", rest_msgs and "롱" in rest_msgs[0])
check("안내가 매매 방식을 드러내지 않음",
      rest_msgs and not any(w in rest_msgs[0] for w in ("물타기", "차", "평단", "마틴", "RSI")),
      rest_msgs[0] if rest_msgs else "")

# 익절에는 휴식 안내가 붙지 않는다
cap.msgs.clear()
bot.long._enter_initial(p)
bot.long._take_profit(up, T0)
check("익절에는 휴식 안내 없음", not [m for m in cap.msgs if "⏸" in m], str(cap.msgs))

# 휴식 시간을 기본 쿨다운과 같게 두면 안내하지 않는다
setup(rest=core.COOLDOWN_SEC)
cap.msgs.clear()
bot.long._enter_initial(p)
bot.long._stop_loss(deep, T0)
check("휴식이 기본과 같으면 안내 안 함", not [m for m in cap.msgs if "⏸" in m], str(cap.msgs))

section("G. 분 단위도 문구가 맞는다")
setup(rest=1800)
cap.msgs.clear()
bot.long._enter_initial(p)
bot.long._stop_loss(deep, T0)
m = [x for x in cap.msgs if "⏸" in x]
check("30분이면 '30분'으로 표시", m and "30분" in m[0], m[0] if m else "")
setup(rest=7200)
cap.msgs.clear()
bot.long._enter_initial(p)
bot.long._stop_loss(deep, T0)
m = [x for x in cap.msgs if "⏸" in x]
check("2시간이면 '2시간'으로 표시", m and "2시간" in m[0], m[0] if m else "")

section("H. 두 빌드 모두 1시간으로 걸려 있는가")
gui = open(os.path.join(R, "hedged_martingale_bot_gui.py"), encoding="utf-8").read()
check("배포용에 고정값 있음", "FIXED_SL_REST_SEC = 3600" in gui)
check("배포용이 시작할 때 적용", "core.STOP_LOSS_COOLDOWN_SEC = FIXED_SL_REST_SEC" in gui)
check("배포용 화면에 표시", '("손절 후 휴식"' in gui)
check("엔진 기본값도 1시간", core.__dict__.get("STOP_LOSS_COOLDOWN_SEC") is not None)
import importlib
fresh = importlib.reload(core)
eq("엔진 기본값 3600초", fresh.STOP_LOSS_COOLDOWN_SEC, 3600)

print()
sys.exit(0 if report("검증 14 — 손절 후 휴식") else 1)
