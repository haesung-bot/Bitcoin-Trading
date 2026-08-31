# -*- coding: utf-8 -*-
"""최종 검증 3 — 실거래 주문 경로 + 실제 시장데이터 종단."""
import sys, os, json, math, logging, urllib.request, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from v_common import *

import hedged_martingale_bot as core
from hedged_martingale_bot import HedgedMartingaleBot, Side, Indicators, PaperBroker, TelegramNotifier

core.logger.setLevel(logging.CRITICAL)
CLOCK = Clock()
core.time = CLOCK
SP = "/tmp/claude-0/-home-user-Bitcoin-Trading/38b84593-71b1-5523-b5e5-807ae97abc7b/scratchpad"


class FakeCcxt:
    def __init__(self, eid, contract_size=0.0001, min_amount=1.0, decimals=0):
        self.id = eid
        self.cs = contract_size
        self.min_amount = min_amount
        self.decimals = decimals
        self.created = []
        self.lev = []
        self.mm = []
        self.pm = []
        self.loaded = False
        self.positions = []
        self.bal = 1600.0

    def load_markets(self, reload=False):
        self.loaded = True
        return {}

    def market(self, s):
        if not self.loaded:
            raise RuntimeError(f"{self.id} markets not loaded")
        return {"contractSize": self.cs, "symbol": s,
                "limits": {"amount": {"min": self.min_amount}}, "precision": {"amount": 1}}

    def amount_to_precision(self, s, amount):
        a = float(amount)
        if a < self.min_amount:
            raise Exception(f"{self.id} amount must be greater than minimum amount precision of {self.min_amount}")
        return str(int(a)) if self.decimals <= 0 else f"{a:.{self.decimals}f}"

    def fetch_balance(self, params=None):
        return {"USDT": {"free": self.bal, "total": self.bal}}

    def fetch_positions(self, symbols=None, params=None):
        return self.positions

    def set_leverage(self, l, s=None, params=None):
        self.lev.append((l, s, dict(params or {})))

    def set_margin_mode(self, m, s=None, params=None):
        self.mm.append((m, s))

    def set_position_mode(self, hedged, symbol=None, params=None):
        self.pm.append((hedged, symbol))

    def create_order(self, symbol, type_, side, amount, price=None, params=None):
        self.created.append((symbol, type_, side, amount, price, dict(params or {})))
        return {"id": "1"}


def broker(name, **kw):
    fake = FakeCcxt(core.EXCHANGE_OPTIONS[name]["ccxt_ids"][0], **kw)
    br = core.LiveBroker.__new__(core.LiveBroker)
    br.exchange = fake
    br.exchange_name = name
    br.symbol = core.EXCHANGE_SYMBOLS[name]
    br._last_balance_error = None
    br.last_error_kind = None
    fake.load_markets()
    return br, fake


defaults(core)

# ══════════════════════════════════════════════════════════
section("16. 거래소별 양방향 주문 파라미터")

for name in core.EXCHANGE_OPTIONS:
    core.LEVERAGE = 10
    br, fk = broker(name)
    try:
        br.fill_order(Side.LONG, True, 0.02, 60000.0)
        br.fill_order(Side.LONG, False, 0.02, 60000.0)
    except Exception as e:
        check(f"{name}: 주문 예외 없음", False, str(e)[:80])
        continue
    eq(f"{name}: 진입/청산 2건", len(fk.created), 2)
    ep, cp = fk.created[0][5], fk.created[1][5]
    if name == "Binance":
        eq(f"{name}: positionSide=LONG", ep.get("positionSide"), "LONG")
        check(f"{name}: 청산에 reduceOnly 없음", "reduceOnly" not in cp, str(cp))
    else:
        check(f"{name}: 청산 reduceOnly", cp.get("reduceOnly") is True, str(cp))
        check(f"{name}: 진입 reduceOnly 없음", not ep.get("reduceOnly"), str(ep))
    check(f"{name}: 롱 buy/sell", fk.created[0][2] == "buy" and fk.created[1][2] == "sell")
    fk.created.clear()
    br.fill_order(Side.SHORT, True, 0.02, 60000.0)
    check(f"{name}: 숏 진입 sell", fk.created[0][2] == "sell")
    if name == "Binance":
        eq(f"{name}: 숏 positionSide", fk.created[0][5].get("positionSide"), "SHORT")

# ══════════════════════════════════════════════════════════
section("17. 증거금 모드 설정")

core.MARGIN_MODE = "cross"
for name in core.EXCHANGE_OPTIONS:
    br, fk = broker(name)
    br._setup_account(25)
    check(f"{name}: 양방향 설정 시도", fk.pm and fk.pm[0][0] is True)
    if name == "Gate.io":
        check(f"{name}: set_leverage에 marginMode=cross",
              fk.lev and fk.lev[0][2].get("marginMode") == "cross", str(fk.lev[:1]))
        check(f"{name}: set_margin_mode 미호출(미지원)", not fk.mm)
    else:
        check(f"{name}: set_margin_mode('cross')", fk.mm == [("cross", br.symbol)], str(fk.mm))
        check(f"{name}: 레버리지 설정", fk.lev and fk.lev[0][0] == 25)
core.MARGIN_MODE = "isolated"
br, fk = broker("Gate.io")
br._setup_account(25)
eq("격리 선택 시 marginMode=isolated", fk.lev[0][2].get("marginMode"), "isolated")
core.MARGIN_MODE = "cross"

# ══════════════════════════════════════════════════════════
section("18. 계약 변환 / 잔고 / 포지션 조회")

core.LEVERAGE = 30
br, fk = broker("Gate.io", contract_size=0.0001, min_amount=1.0)
br.fill_order(Side.LONG, True, 0.0258, 63818.0)
eq("0.0258 BTC → 258 계약", float(fk.created[0][3]), 258.0)
raised = None
try:
    br.fill_order(Side.LONG, True, 0.00005, 63818.0)
except Exception as e:
    raised = e
check("최소 미달 → 예외", raised is not None)
check("오류에 잔고 안내", raised and ("최소" in str(raised) or "잔고" in str(raised)))
br2, fk2 = broker("Binance", contract_size=1.0, min_amount=0.001, decimals=3)
br2.fill_order(Side.LONG, True, 0.0258, 63818.0)
check("코인단위 거래소 정상", len(fk2.created) == 1)

br3, fk3 = broker("Gate.io")
fk3.bal = 1234.56
eq("USDT 잔고", br3.get_balance(), 1234.56)


class Boom(FakeCcxt):
    def fetch_balance(self, params=None):
        raise RuntimeError("network down")


br3.exchange = Boom("gate")
br3.exchange.load_markets()
eq("조회 실패 시 0 (진입 차단)", br3.get_balance(), 0.0)


class FreeZero(FakeCcxt):
    def fetch_balance(self, params=None):
        return {"USDT": {"free": 0.0, "total": 987.0}}


br3.exchange = FreeZero("gate")
br3.exchange.load_markets()
eq("free=0이면 total", br3.get_balance(), 987.0)

br4, fk4 = broker("Gate.io")
fk4.positions = [{"symbol": "BTC/USDT:USDT", "side": "long", "contracts": 258.0,
                  "entryPrice": 63818.0, "contractSize": 0.0001}]
p = br4.fetch_position(Side.LONG)
eq("계약→BTC 환산", round(p["qty"], 8), 0.0258)
eq("평단가", p["entry_price"], 63818.0)
ps = br4.fetch_position(Side.SHORT)
check("없는 방향은 0", ps is None or ps["qty"] == 0)

# ══════════════════════════════════════════════════════════
section("19. 종단 — 실거래 경로 전체 사이클")

defaults(core, lev=30, pos=0.02)
br, fk = broker("Gate.io", contract_size=0.0001, min_amount=1.0)
br.apply_pnl = lambda p: None
sp = fresh_path()
bot = HedgedMartingaleBot(br, Notifier(), "LIVE", state_path=sp)
L = bot.long
fk.created.clear()
L.on_tick(63785.30, 10.0, (63785, 64500, 63800), CLOCK.time())
eq("실거래 1차 진입", L.step, 1)
check("주문 정수 계약", float(fk.created[0][3]) == int(float(fk.created[0][3])))
pred = core.martingale_ladder(Side.LONG, L.fills)
for stage in (2, 3, 4):
    t = pred[stage - 1]["price"]
    L.on_tick(t, 50, (t, t * 1.1, t * 0.9), CLOCK.time())
    eq(f"실거래 {stage}차", L.step, stage)
amts = [float(c[3]) for c in fk.created]
check("주문 수량 1:2:4:8", [round(a / amts[0]) for a in amts] == [1, 2, 4, 8], str(amts))
bot._save_state()
total = sum(amts)
fk.positions = [{"symbol": "BTC/USDT:USDT", "side": "long", "contracts": total,
                 "entryPrice": L.avg_price, "contractSize": 0.0001}]
snap = (L.step, round(L.total_qty, 8), round(L.avg_price, 2))
bot2 = HedgedMartingaleBot(br, Notifier(), "LIVE", state_path=sp)
check("실거래 재시작 복원",
      (bot2.long.step, round(bot2.long.total_qty, 8), round(bot2.long.avg_price, 2)) == snap)
fk.created.clear()
tp = bot2.long.avg_price * (1 + core.TP_PCT + 1e-9)
bot2.long.on_tick(tp, 50, (tp, tp * 1.1, tp * 0.9), CLOCK.time())
eq("재시작 후 익절", bot2.long.step, 0)
check("청산 reduceOnly", fk.created[-1][5].get("reduceOnly") is True)
eq("청산 수량 = 전량", float(fk.created[-1][3]), total)

# ══════════════════════════════════════════════════════════
section("20. 종단 — 실제 BTC 데이터 + 반복 재시작")


def kraken(iv=15):
    u = f"https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval={iv}"
    d = json.loads(urllib.request.urlopen(u, timeout=30).read())
    k = [x for x in d["result"] if x != "last"][0]
    return [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])] for r in d["result"][k]]


try:
    C = kraken(15)
    t0 = datetime.datetime.utcfromtimestamp(C[0][0])
    t1 = datetime.datetime.utcfromtimestamp(C[-1][0])
    print(f"  데이터: {len(C)}봉  {t0:%m/%d %H:%M} ~ {t1:%m/%d %H:%M}  "
          f"{C[0][4]:,.0f} → {C[-1][4]:,.0f} ({(C[-1][4]/C[0][4]-1)*100:+.1f}%)\n")
except Exception as e:
    print(f"  시세 조회 실패: {e}")
    C = None

if C:
    for hedge_on in (False, True):
        defaults(core, lev=20, pos=0.02)
        core.HEDGE_AT_STEP = 3 if hedge_on else 0
        core.MAX_CONSECUTIVE_SL = 10 ** 9
        closes = [x[4] for x in C]
        ex = Exchange(balance=1600.0, fee=0.0005)
        sp = fresh_path()
        bot = HedgedMartingaleBot(ex, Notifier(), "E2E", state_path=sp)
        bad = []
        restarts = 0
        for i in range(100, len(C)):
            t, o, h, l, c = C[i]
            seq = (o, l, h, c) if c >= o else (o, h, l, c)
            w0 = closes[i - 99:i]
            for p in seq:
                bot.on_price(p, w0 + [p], CLOCK.time())
                CLOCK.advance(225)
                for m, k in ((bot.long, "LONG"), (bot.short, "SHORT")):
                    hedge_extra = 0.0
                    opp = Side.SHORT if m.side == Side.LONG else Side.LONG
                    hh = bot.hedges.get(opp)
                    if hh:
                        hedge_extra = hh.qty
                    exq = ex.pos.get(k, {"qty": 0.0})["qty"]
                    if abs(exq - (m.total_qty + hedge_extra)) > 1e-8:
                        bad.append(f"bar{i} {k} 내부{m.total_qty:.6f}+헷지{hedge_extra:.6f} vs 거래소{exq:.6f}")
                    if m.step > core.MAX_STEPS:
                        bad.append(f"bar{i} {k} step {m.step}")
                    if m.in_position and len(m.fills) != m.step:
                        bad.append(f"bar{i} {k} fills {len(m.fills)}!={m.step}")
                    if m.in_position and m.step >= 2:
                        for a, bb2 in zip(m.fills, m.fills[1:]):
                            if abs(bb2.qty - a.qty * 2) > max(a.qty * 1e-6, 1e-12):
                                bad.append(f"bar{i} {k} 배증 깨짐")
            if i % 120 == 0:
                before = [(m.step, round(m.total_qty, 10)) for m in (bot.long, bot.short)]
                hb = {k: (round(v.qty, 10), round(v.price, 4)) for k, v in bot.hedges.items()}
                bot = HedgedMartingaleBot(ex, Notifier(), "E2E", state_path=sp)
                after = [(m.step, round(m.total_qty, 10)) for m in (bot.long, bot.short)]
                ha = {k: (round(v.qty, 10), round(v.price, 4)) for k, v in bot.hedges.items()}
                restarts += 1
                if before != after or hb != ha:
                    bad.append(f"bar{i} 재시작 불일치")
        tag = "헷지 ON" if hedge_on else "헷지 OFF"
        check(f"[{tag}] 불변조건 위반 없음", not bad, f"{len(bad)}건: {bad[:2]}")
        check(f"[{tag}] 재시작 {restarts}회 모두 유지", restarts > 0, f"{restarts}회")
        check(f"[{tag}] 잔고 양수", ex.balance > 0, f"${ex.balance:,.2f}")
        core.HEDGE_AT_STEP = 0

print()
sys.exit(0 if report("검증 3 — 실거래 경로·종단") else 1)
