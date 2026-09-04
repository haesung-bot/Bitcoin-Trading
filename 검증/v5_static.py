# -*- coding: utf-8 -*-
"""최종 검증 5 — 정적 점검(구문·비밀·버전·기본값)."""
import sys, os, ast, re, json, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from v_common import *
R = "/home/user/Bitcoin-Trading"
FILES = ["hedged_martingale_bot.py", "hedged_martingale_bot_gui.py", "martingale_bot_mine.py"]

section("29. 구문 / import")
for f in FILES:
    src = open(os.path.join(R, f), encoding="utf-8").read()
    try:
        ast.parse(src); check(f"{f} 구문", True)
    except SyntaxError as e:
        check(f"{f} 구문", False, str(e))

section("30. 비밀정보 유출 점검")
SECRETS = [
    ("게이트 API Key", "68b5d63d0b692801f43fba329bad52f3"),
    ("게이트 Secret", "ae611a7f687583cf7d90810e284d2f15cea1d2a8a4d22937aba1bd27e9408eed"),
    ("텔레그램 봇 토큰", "8715993070:AAF24cb1k_jR-pZIxQID_kFsCOnMxk2cTC0"),
]
for f in FILES:
    src = open(os.path.join(R, f), encoding="utf-8").read()
    for label, s in SECRETS:
        check(f"{f}: {label} 없음", s not in src)
# 하드코딩된 키 패턴
for f in FILES:
    src = open(os.path.join(R, f), encoding="utf-8").read()
    m = re.findall(r'(?:api_?key|secret)\s*=\s*["\'][A-Za-z0-9]{20,}["\']', src, re.I)
    check(f"{f}: 하드코딩 키 패턴 없음", not m, str(m[:1]))
    m2 = re.findall(r'\d{9,10}:AA[A-Za-z0-9_-]{30,}', src)
    check(f"{f}: 텔레그램 토큰 패턴 없음", not m2)

section("31. 버전 / 기본값")
import hedged_martingale_bot as core
print(f"  엔진 버전: {core.VERSION}")
check("VERSION 형식", re.fullmatch(r"\d+\.\d+\.\d+", core.VERSION) is not None, core.VERSION)
DEFAULTS = [("STEP_TRIGGER_PCT", 0.012), ("TP_PCT", 0.003), ("STOP_LOSS_PCT", 0.025),
            ("MAX_STEPS", 4), ("MAX_CONSECUTIVE_SL", 3), ("COOLDOWN_SEC", 180),
            ("RSI_PERIOD", 14), ("RSI_LONG_TRIGGER", 40.0), ("RSI_SHORT_TRIGGER", 60.0),
            ("BB_PERIOD", 20), ("BB_STDDEV_MULT", 2.0), ("TIMEFRAME", "15m"),
            ("TREND_EMA_PERIOD", 0), ("HEDGE_AT_STEP", 0),
            ("SHOW_QTY_DETAIL", True), ("TELEGRAM_SHOW_BALANCE", True), ("MARGIN_MODE", "cross")]
for k, v in DEFAULTS:
    eq(f"{k}", getattr(core, k), v)

section("32. 배포용/개인용 격리")
import importlib
sub = subprocess.run([os.environ.get("TKPY", sys.executable), "-c", """
import sys, os, tempfile
sys.path.insert(0, "/home/user/Bitcoin-Trading")
H=tempfile.mkdtemp(); os.environ["HOME"]=H
os.path.expanduser = lambda p: p.replace("~",H) if p.startswith("~") else p
import hedged_martingale_bot as core, json
d={}
import hedged_martingale_bot_gui as g
d["dist_cfg"]=g.CONFIG_PATH; d["dist_state"]=core.STATE_PATH; d["dist_trade"]=core.TRADE_LOG_PATH
d["dist_qty"]=core.SHOW_QTY_DETAIL; d["dist_bal"]=core.TELEGRAM_SHOW_BALANCE
import martingale_bot_mine as m
d["mine_cfg"]=m.CONFIG_PATH; d["mine_state"]=core.STATE_PATH; d["mine_trade"]=core.TRADE_LOG_PATH
d["mine_qty"]=core.SHOW_QTY_DETAIL; d["mine_bal"]=core.TELEGRAM_SHOW_BALANCE
print("@@"+json.dumps(d))
"""], capture_output=True, text=True, timeout=180,
    env=dict(os.environ, PATH=os.environ.get("PATH","")))
line = [l for l in sub.stdout.splitlines() if l.startswith("@@")]
if line:
    d = json.loads(line[0][2:])
    check("설정 경로 분리", d["dist_cfg"] != d["mine_cfg"])
    check("상태 경로 분리", d["dist_state"] != d["mine_state"])
    check("기록 경로 분리", d["dist_trade"] != d["mine_trade"])
    check("배포용 수량 숨김", d["dist_qty"] is False)
    check("개인용 USDT 표시", d["mine_qty"] is False)
    check("배포용 텔레그램 잔고 표시", d["dist_bal"] is True)
    check("개인용 텔레그램 잔고 숨김", d["mine_bal"] is False)
else:
    check("격리 확인 실행", False, sub.stderr[-200:])

section("33. 배포용 로그가 전략을 노출하지 않는가")
import logging
core.logger.setLevel(logging.CRITICAL)
from hedged_martingale_bot import HedgedMartingaleBot, PaperBroker, TelegramNotifier
core.SHOW_QTY_DETAIL = True
cap=[]
class N(TelegramNotifier):
    def __init__(s): super().__init__("","")
    def send(s,t,telegram_text=None): cap.append(t)
core.LEVERAGE, core.INITIAL_MARGIN_PCT = 10, 0.02
b=HedgedMartingaleBot(PaperBroker(1600.0), N(), "LIVE")
L=b.long; L._enter_initial(65000.0)
for _ in range(3):
    t=L.avg_price*(1-core.STEP_TRIGGER_PCT-1e-9)
    L.on_tick(t,50,(t,t*1.1,t*0.9),1000.0)
sl=L.avg_price*(1-core.STOP_LOSS_PCT-1e-9)
L.on_tick(sl,50,(sl,sl*1.1,sl*0.9),2000.0)
joined="\n".join(cap)
for w in ("차 진입","물타기","마틴","RSI","볼린저","단계","EMA"):
    check(f"배포용 로그에 '{w}' 없음", w not in joined, joined[:80])
check("배포용 로그에 금액/가격은 있음", "금액" in joined and "가격" in joined)

print()
sys.exit(0 if report("검증 5 — 정적 점검") else 1)
