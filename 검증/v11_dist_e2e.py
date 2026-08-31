# -*- coding: utf-8 -*-
"""검증 11 — 배포용 고정설정 종단 검증.

배포용 화면 파일에 박혀 있는 고정값(FIXED_LEVERAGE / FIXED_POS_PCT / FIXED_MAX_STEPS)을
그대로 읽어와서 검사한다. 고정값을 바꾸면 이 파일을 손대지 않아도 새 값 기준으로 검증된다.
"""
import sys, os, re, json, math, logging, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from v_common import *
import hedged_martingale_bot as core
from hedged_martingale_bot import HedgedMartingaleBot, Side, Fill
CLOCK = Clock()

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUI_SRC = open(os.path.join(R, "hedged_martingale_bot_gui.py"), encoding="utf-8").read()
LEV = int(re.search(r"^FIXED_LEVERAGE\s*=\s*(\d+)", GUI_SRC, re.M).group(1))
POS = float(re.search(r"^FIXED_POS_PCT\s*=\s*([\d.]+)", GUI_SRC, re.M).group(1))
STEPS = int(re.search(r"^FIXED_MAX_STEPS\s*=\s*(\d+)", GUI_SRC, re.M).group(1))
print(f"\n  배포용 고정값: 레버리지 {LEV}배 / 진입금액 {POS*100:g}% / 최대 {STEPS}단계\n")

defaults(core, lev=LEV, pos=POS)
core.MAX_STEPS = STEPS
core.HEDGE_AT_STEP = 0
core.MINIMAL_LOG = True
core.SHOW_QTY_DETAIL = False
core.MAX_CONSECUTIVE_SL = 10 ** 9

section("A. 고정값 자체의 안전성")
ladder = sum(2 ** i for i in range(STEPS))       # 3단계면 1+2+4 = 7
margin_one = POS * ladder
notional = POS * LEV * ladder                    # 잔고 대비 명목 배수
cross_dist = 1.0 / notional                      # 교차 청산까지의 가격 여유
safe_iso = 1.0 / (1 - (1 - core.STOP_LOSS_PCT) * (1 - 0.005))

check("물타기 끝까지 가도 증거금이 잔고 안", margin_one <= 1.0, f"한 방향 {margin_one*100:.0f}%")
check("양방향 동시에도 증거금이 잔고 안", margin_one * 2 <= 1.0, f"양방향 {margin_one*200:.0f}%")
check("교차 청산거리 > 하드손절 폭", cross_dist > core.STOP_LOSS_PCT,
      f"청산 {cross_dist*100:.2f}% vs 손절 {core.STOP_LOSS_PCT*100:.2f}%")
check("2차 물타기 전에 청산되지 않음", cross_dist > core.STEP_TRIGGER_PCT,
      f"청산 {cross_dist*100:.2f}% vs 2차 {core.STEP_TRIGGER_PCT*100:.2f}%")
if LEV <= safe_iso:
    check("격리에서도 안전 한도 이내", True, f"{LEV}배 ≤ 한도 {safe_iso:.1f}배")
else:
    check("격리는 시작 차단 대상", core.ALLOW_UNSAFE_ISOLATED is False,
          f"{LEV}배 > 한도 {safe_iso:.1f}배 → 격리면 시작 안 함")

section("B. 로그가 전략을 드러내지 않는가")
buf = []


class Cap(logging.Handler):
    def emit(self, r):
        buf.append(self.format(r))


h = Cap()
h.setFormatter(logging.Formatter("%(message)s"))
old_handlers = core.logger.handlers
core.logger.handlers = [h]
core.logger.setLevel(logging.INFO)

ex = Exchange(balance=1600.0, fee=0.0005)
bot = HedgedMartingaleBot(ex, core.TelegramNotifier("", ""), "DIST", state_path=fresh_path())
p = 65000.0
bot.long._enter_initial(p); CLOCK.advance(900)
bot.long._add_martingale(p * 0.988); CLOCK.advance(900)
bot.long._add_martingale(p * 0.976); CLOCK.advance(900)
bot._log_heartbeat(p * 0.976)
bot.long._take_profit(p * 0.99, CLOCK.time())
core.logger.handlers = old_handlers
text = "\n".join(buf)

for word in ("차 진입", "물타기", "마틴", "RSI", "볼린저", "단계", "평단", "레버리지", "BTC"):
    check(f"진입/청산 줄에 '{word}' 없음", word not in text, text[:110].replace("\n", " / "))
check("금액·가격은 남음", "USDT" in text and re.search(r"\d,\d{3}\.\d{2}", text) is not None, text[:110])
check("하트비트는 '보유'만", "보유" in text)

# 시작 줄은 반대로 레버리지와 진입금액을 드러내야 한다.
buf.clear()
core.logger.handlers = [h]


class FakeEx:
    def set_leverage(self, lev, sym, params=None): pass
    def set_margin_mode(self, mode, sym): pass


lb = core.LiveBroker.__new__(core.LiveBroker)
lb.exchange = FakeEx()
lb.exchange_name = "Gate.io"
lb.symbol = "BTC/USDT:USDT"
lb.margin_mode_ok = False
lb.read_margin_mode = lambda: "cross"
lb._setup_margin_mode(LEV)
core.logger.handlers = old_handlers
start_line = "\n".join(buf)
check(f"시작 줄에 '레버리지 {LEV}배'", f"레버리지 {LEV}배" in start_line, start_line[:120])
check(f"시작 줄에 '진입금액 {POS*100:g}%'", f"진입금액 {POS*100:g}%" in start_line, start_line[:120])
check("시작 줄에 '교차(Cross)'", "교차(Cross)" in start_line, start_line[:120])

section(f"C. 실제 BTC 데이터 종단 ({LEV}배 / {POS*100:g}% / {STEPS}단계)")


def kraken(iv=15):
    u = f"https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval={iv}"
    d = json.loads(urllib.request.urlopen(u, timeout=30).read())
    k = [x for x in d["result"] if x != "last"][0]
    return [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])] for r in d["result"][k]]


try:
    C = kraken(15)
    print(f"  데이터: {len(C)}봉  {C[0][4]:,.0f} → {C[-1][4]:,.0f} "
          f"({(C[-1][4]/C[0][4]-1)*100:+.1f}%)\n")
except Exception as e:
    print(f"  시세 조회 실패: {e}")
    C = None

if C:
    closes = [x[4] for x in C]
    ex = Exchange(balance=1600.0, fee=0.0005)
    sp = fresh_path()
    bot = HedgedMartingaleBot(ex, Notifier(), "DIST", state_path=sp)
    bad, restarts, peak_margin, min_bal = [], 0, 0.0, 1e18
    for i in range(100, len(C)):
        t, o, hi, lo, c = C[i]
        seq = (o, lo, hi, c) if c >= o else (o, hi, lo, c)
        w0 = closes[i - 99:i]
        for px in seq:
            bot.on_price(px, w0 + [px], CLOCK.time())
            CLOCK.advance(225)
            used = 0.0
            for m, k in ((bot.long, "LONG"), (bot.short, "SHORT")):
                exq = ex.pos.get(k, {"qty": 0.0})["qty"]
                if abs(exq - m.total_qty) > 1e-8:
                    bad.append(f"bar{i} {k} 내부{m.total_qty:.8f} vs 거래소{exq:.8f}")
                if m.step > STEPS:
                    bad.append(f"bar{i} {k} step {m.step} > {STEPS}")
                if m.in_position and len(m.fills) != m.step:
                    bad.append(f"bar{i} {k} fills {len(m.fills)} != step {m.step}")
                if m.in_position and m.step >= 2:
                    for a, b2 in zip(m.fills, m.fills[1:]):
                        if abs(b2.qty - a.qty * 2) > max(a.qty * 1e-6, 1e-12):
                            bad.append(f"bar{i} {k} 배증 깨짐")
                used += m.total_qty * px / LEV
            peak_margin = max(peak_margin, used / max(ex.balance, 1e-9))
            min_bal = min(min_bal, ex.balance)
        if i % 150 == 0:
            before = [(m.step, round(m.total_qty, 10)) for m in (bot.long, bot.short)]
            bot = HedgedMartingaleBot(ex, Notifier(), "DIST", state_path=sp)
            after = [(m.step, round(m.total_qty, 10)) for m in (bot.long, bot.short)]
            restarts += 1
            if before != after:
                bad.append(f"bar{i} 재시작 불일치 {before} vs {after}")
    check("불변조건 위반 없음", not bad, f"{len(bad)}건: {bad[:2]}")
    check(f"재시작 {restarts}회 모두 상태 유지", restarts > 0 and not any("재시작" in b for b in bad))
    check(f"{STEPS + 1}차 진입 없음", not any("step" in b for b in bad))
    check("증거금 사용률이 잔고 안", peak_margin < 1.0, f"최대 {peak_margin*100:.1f}%")
    check("잔고 양수 유지", min_bal > 0, f"최저 ${min_bal:,.2f}")
    print(f"  최종 잔고: ${ex.balance:,.2f}  (시작 $1,600.00)")

print()
sys.exit(0 if report("검증 11 — 배포용 고정설정") else 1)
