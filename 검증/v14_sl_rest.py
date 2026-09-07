# -*- coding: utf-8 -*-
"""검증 14 — 손절 후 휴식(양방향).

하드손절이 나면 STOP_LOSS_COOLDOWN_SEC(기본 1시간) 동안 롱·숏 양쪽 모두 새로 들어가지
않는다. 익절은 기존 짧은 쿨다운(COOLDOWN_SEC) 그대로다.
멈추는 것은 '새 진입'뿐이고, 이미 열려 있는 포지션은 그대로 관리한다.
"""
import sys, os, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from v_common import *
import hedged_martingale_bot as core
from hedged_martingale_bot import HedgedMartingaleBot, Side, Fill

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T0 = 1_800_000_000.0        # 야간 정지와 무관한 시각


def setup(rest=3600, both=True):
    defaults(core, lev=25, pos=0.03)
    core.MAX_STEPS = 3
    core.HEDGE_AT_STEP = 0
    core.MAX_CONSECUTIVE_SL = 10 ** 9
    core.QUIET_START_HOUR = core.QUIET_END_HOUR = -1
    core.QUIET_STOP_LOSS_STEP = 0
    core.STOP_LOSS_COOLDOWN_SEC = rest
    core.STOP_LOSS_REST_BOTH_SIDES = both


def make_bot(balance=1600.0, notifier=None):
    ex = Exchange(balance=balance, fee=0.0005)
    return ex, HedgedMartingaleBot(ex, notifier or Notifier(), "REST", state_path=fresh_path())


def falling(last):
    return [last * (1 + 0.02 * (1 - i / 99.0)) for i in range(99)] + [last]


def rising(last):
    return [last * (1 - 0.02 * (1 - i / 99.0)) for i in range(99)] + [last]


p = 65000.0
deep = p * (1 - core.STOP_LOSS_PCT * 1.6)

section("A. 손절이 나면 양쪽 모두 1시간 쉰다")
setup()
ex, bot = make_bot()
bot.long._enter_initial(p)
bot.long._stop_loss(deep, T0)
check("손절로 포지션 정리됨", not bot.long.in_position)
eq("손절 난 롱: 정확히 1시간", round(bot.long.cooldown_until - T0), 3600)
check("반대편 숏에도 휴식이 걸림", bot.short.cooldown_until is not None)
eq("숏도 같은 시각까지", round(bot.short.cooldown_until - T0), 3600)
check("30분 뒤 롱 아직 쉼", bot.long._in_cooldown(T0 + 1800))
check("30분 뒤 숏도 아직 쉼", bot.short._in_cooldown(T0 + 1800))
check("59분 뒤 양쪽 다 쉼",
      bot.long._in_cooldown(T0 + 3540) and bot.short._in_cooldown(T0 + 3540))
check("1시간 1초 뒤 양쪽 다 풀림",
      not bot.long._in_cooldown(T0 + 3601) and not bot.short._in_cooldown(T0 + 3601))

section("B. 쉬는 동안 양쪽 다 진입하지 않는다")
setup()
ex, bot = make_bot()
bot.long._enter_initial(p)
bot.long._stop_loss(deep, T0)
bot.on_price(p, falling(p), T0 + 600)
check("10분 뒤: 롱 진입 안 함", not bot.long.in_position)
bot.on_price(p, rising(p), T0 + 900)
check("15분 뒤: 숏도 진입 안 함", not bot.short.in_position)
bot.on_price(p, falling(p), T0 + 3000)
check("50분 뒤: 여전히 양쪽 다 대기",
      not bot.long.in_position and not bot.short.in_position)
bot.on_price(p, falling(p), T0 + 3700)
check("1시간 뒤: 롱 진입 재개", bot.long.in_position)

setup()
ex, bot = make_bot()
bot.short._enter_initial(p)
bot.short._stop_loss(p * (1 + core.STOP_LOSS_PCT * 1.6), T0)
bot.on_price(p, rising(p), T0 + 3700)
check("숏 손절 뒤에도 1시간 후 재개", bot.short.in_position)

section("C. 반대편에 열려 있는 포지션은 계속 관리한다")
setup()
ex, bot = make_bot()
bot.long._enter_initial(p)
bot.short._enter_initial(p)              # 숏도 이미 보유 중
qty_before = bot.short.total_qty
bot.long._stop_loss(deep, T0)
check("숏 포지션은 청산되지 않음", bot.short.in_position)
eq("숏 수량 그대로", round(bot.short.total_qty, 10), round(qty_before, 10))
check("숏에도 휴식은 걸림(새 진입만 막힘)", bot.short._in_cooldown(T0 + 600))

# 쉬는 중에도 숏의 익절은 그대로 동작해야 한다
tp_price = bot.short.avg_price * (1 - core.TP_PCT * 1.2)
bal_before = ex.balance
bot.on_price(tp_price, rising(tp_price), T0 + 900)
check("쉬는 중에도 숏 익절은 됨", not bot.short.in_position)
check("익절로 잔고 증가", ex.balance > bal_before, f"${bal_before:,.2f} → ${ex.balance:,.2f}")

# 쉬는 중에도 물타기는 동작해야 한다(이미 연 포지션의 관리)
setup()
ex, bot = make_bot()
bot.long._enter_initial(p)
bot.short._enter_initial(p)
step_before = bot.short.step
bot.long._stop_loss(deep, T0)
add_price = bot.short.avg_price * (1 + core.STEP_TRIGGER_PCT * 1.3)
bot.on_price(add_price, rising(add_price), T0 + 900)
eq("쉬는 중에도 숏 물타기는 됨", bot.short.step, step_before + 1)

section("D. 더 긴 휴식을 짧게 덮어쓰지 않는다")
setup()
ex, bot = make_bot()
bot.short.cooldown_until = T0 + 7200          # 숏이 이미 2시간 쉬는 중
bot.long._enter_initial(p)
bot.long._stop_loss(deep, T0)
eq("숏의 더 긴 휴식이 유지됨", round(bot.short.cooldown_until - T0), 7200)

setup()
ex, bot = make_bot()
bot.short.cooldown_until = T0 + 600           # 숏이 10분만 쉬는 중
bot.long._enter_initial(p)
bot.long._stop_loss(deep, T0)
eq("짧은 휴식은 1시간으로 늘어남", round(bot.short.cooldown_until - T0), 3600)

section("E. 익절은 예전대로 짧게, 반대편은 건드리지 않는다")
setup()
ex, bot = make_bot()
bot.long._enter_initial(p)
up = p * (1 + core.TP_PCT * 1.2)
bot.long._take_profit(up, T0)
eq("익절 쿨다운은 기본값", round(bot.long.cooldown_until - T0), core.COOLDOWN_SEC)
check("익절은 반대편에 휴식을 걸지 않음", bot.short.cooldown_until is None)
bot.on_price(p, rising(p), T0 + 60)
check("익절 직후 숏은 바로 진입 가능", bot.short.in_position)

section("F. 껐다 켜도 양쪽 휴식이 유지된다")
setup()
ex = Exchange(balance=1600.0, fee=0.0005)
sp = fresh_path()
bot = HedgedMartingaleBot(ex, Notifier(), "REST", state_path=sp)
bot.long._enter_initial(p)
bot.long._stop_loss(deep, T0)
bot._save_state()
lu, su = bot.long.cooldown_until, bot.short.cooldown_until

bot2 = HedgedMartingaleBot(ex, Notifier(), "REST", state_path=sp)
eq("재시작 후 롱 휴식 유지", bot2.long.cooldown_until, lu)
eq("재시작 후 숏 휴식 유지", bot2.short.cooldown_until, su)
bot2.on_price(p, falling(p), T0 + 600)
check("재시작 후 10분: 롱 진입 안 함", not bot2.long.in_position)
bot2.on_price(p, rising(p), T0 + 900)
check("재시작 후 15분: 숏도 진입 안 함", not bot2.short.in_position)

section("G. 안내 메시지")
setup()


class Cap:
    def __init__(self):
        self.msgs = []

    def send(self, text, telegram_text=None):
        self.msgs.append(text)


cap = Cap()
ex, bot = make_bot(notifier=cap)
bot.long._enter_initial(p)
cap.msgs.clear()
bot.long._stop_loss(deep, T0)
rest_msgs = [m for m in cap.msgs if "⏸" in m]
check("휴식 안내 나감", len(rest_msgs) == 1, str(cap.msgs))
check("안내에 '롱·숏 모두' 표시", rest_msgs and "롱·숏 모두" in rest_msgs[0],
      rest_msgs[0] if rest_msgs else "")
check("안내에 '1시간' 표시", rest_msgs and "1시간" in rest_msgs[0])
check("안내가 매매 방식을 드러내지 않음",
      rest_msgs and not any(w in rest_msgs[0] for w in ("물타기", "차", "평단", "마틴", "RSI")),
      rest_msgs[0] if rest_msgs else "")

cap.msgs.clear()
bot.long._enter_initial(p)
bot.long._take_profit(up, T0)
check("익절에는 휴식 안내 없음", not [m for m in cap.msgs if "⏸" in m], str(cap.msgs))

setup(rest=core.COOLDOWN_SEC)
cap.msgs.clear()
bot.long._enter_initial(p)
bot.long._stop_loss(deep, T0)
check("휴식이 기본과 같으면 안내 안 함", not [m for m in cap.msgs if "⏸" in m], str(cap.msgs))

setup(rest=1800)
cap.msgs.clear()
bot.long._enter_initial(p)
bot.long._stop_loss(deep, T0)
m = [x for x in cap.msgs if "⏸" in x]
check("30분이면 '30분'으로 표시", m and "30분" in m[0], m[0] if m else "")

section("H. 한쪽만 쉬게 꺼둘 수도 있다")
setup(both=False)
ex, bot = make_bot()
bot.long._enter_initial(p)
bot.long._stop_loss(deep, T0)
eq("롱만 1시간", round(bot.long.cooldown_until - T0), 3600)
check("숏은 휴식 없음", bot.short.cooldown_until is None)
bot.on_price(p, rising(p), T0 + 600)
check("숏은 10분 뒤 바로 진입", bot.short.in_position)

cap2 = Cap()
setup(both=False)
ex, bot = make_bot(notifier=cap2)
bot.long._enter_initial(p)
cap2.msgs.clear()
bot.long._stop_loss(deep, T0)
one = [x for x in cap2.msgs if "⏸" in x]
check("한쪽만일 때는 방향 이름으로 안내", one and "롱은" in one[0], one[0] if one else "")

section("I. 두 빌드에 실제로 걸려 있는가")
gui = open(os.path.join(R, "hedged_martingale_bot_gui.py"), encoding="utf-8").read()
check("배포용 고정값 1시간", "FIXED_SL_REST_SEC = 3600" in gui)
check("배포용 양방향 고정값", "FIXED_SL_REST_BOTH = True" in gui)
check("배포용이 시작할 때 적용", "core.STOP_LOSS_COOLDOWN_SEC = FIXED_SL_REST_SEC" in gui)
check("배포용이 양방향도 적용", "core.STOP_LOSS_REST_BOTH_SIDES = FIXED_SL_REST_BOTH" in gui)
check("배포용 화면에 표시", '("손절 후 휴식"' in gui and "롱·숏 모두" in gui)

import importlib
fresh = importlib.reload(core)
eq("엔진 기본값 3600초", fresh.STOP_LOSS_COOLDOWN_SEC, 3600)
eq("엔진 기본값 양방향", fresh.STOP_LOSS_REST_BOTH_SIDES, True)

print()
sys.exit(0 if report("검증 14 — 손절 후 휴식(양방향)") else 1)
