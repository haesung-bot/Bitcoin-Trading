# -*- coding: utf-8 -*-
"""검증 15 — 이더리움 전용 본인용 빌드.

확인할 것은 두 가지다.
  1) 종목이 ETH로 확실히 바뀌었는가 (브로커·시세조회 두 경로 모두)
  2) BTC 빌드와 파일이 섞이지 않는가 (설정·상태·기록 전부)
그 외 매매 설정은 본인용 BTC 빌드와 완전히 같아야 한다.
"""
import sys, os, time, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from v_common import *

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, R)
os.environ["HOME"] = tempfile.mkdtemp(prefix="ethbot-home-")

import tkinter as tk
from tkinter import messagebox
import hedged_martingale_bot as core
import hedged_martingale_bot_gui as dist
import hedged_martingale_bot_my as my
import hedged_martingale_bot_eth as eth

POPUPS = []
for name in ("showerror", "showinfo", "showwarning"):
    setattr(messagebox, name, lambda *a, **k: POPUPS.append(("popup", a, k)))
messagebox.askyesno = lambda *a, **k: False

root = tk.Tk()
app = eth.EthBotGUI(root)
root.update_idletasks()


def all_text(w):
    out = []

    def walk(x):
        for c in x.winfo_children():
            try:
                t = c.cget("text")
            except Exception:
                t = ""
            if t:
                out.append(str(t))
            walk(c)

    walk(w)
    return " ".join(out)


j = all_text(root)

section("A. 종목이 ETH로 바뀌었는가")
eq("core.SYMBOL", core.SYMBOL, "ETH/USDT:USDT")
check("거래소별 심볼 전부 ETH",
      set(core.EXCHANGE_SYMBOLS.values()) == {"ETH/USDT:USDT"},
      str(set(core.EXCHANGE_SYMBOLS.values())))
check("BTC 심볼이 남아 있지 않음",
      not any("BTC" in v for v in core.EXCHANGE_SYMBOLS.values()))

# 브로커·시세조회가 실제로 ETH를 집는지 (거래소 접속 없이 심볼 해석만 확인)
for name in core.EXCHANGE_OPTIONS:
    resolved = core.EXCHANGE_SYMBOLS.get(name, core.SYMBOL)
    check(f"{name} → ETH", resolved == "ETH/USDT:USDT", resolved)

section("B. BTC 빌드와 파일이 섞이지 않는가")
btc_state = os.path.join(os.path.expanduser("~"), ".hedged_martingale_bot_state.json")
btc_trades = os.path.join(os.path.expanduser("~"), ".hedged_martingale_bot_trades.json")
btc_cfg = os.path.join(os.path.expanduser("~"), ".hedged_martingale_bot_gui_config.json")
check("상태 파일이 BTC와 다름", core.STATE_PATH != btc_state, os.path.basename(core.STATE_PATH))
check("기록 파일이 BTC와 다름", core.TRADE_LOG_PATH != btc_trades,
      os.path.basename(core.TRADE_LOG_PATH))
check("설정 파일이 BTC와 다름", dist.CONFIG_PATH != btc_cfg,
      os.path.basename(dist.CONFIG_PATH))
for path, label in ((core.STATE_PATH, "상태"), (core.TRADE_LOG_PATH, "기록"),
                    (dist.CONFIG_PATH, "설정")):
    check(f"{label} 파일 이름에 eth 표시", "eth" in os.path.basename(path).lower(),
          os.path.basename(path))
check("세 파일 경로가 서로 다름",
      len({core.STATE_PATH, core.TRADE_LOG_PATH, dist.CONFIG_PATH}) == 3)

section("C. 화면에서 종목을 헷갈릴 수 없는가")
check("제목에 ETH", "ETH" in root.title(), root.title())
check("배너에 이더리움 표시", "이더리움) 전용" in j)
check("배너에 심볼 전체 표시", "ETH/USDT:USDT" in j)
check("같은 계정 동시 실행 경고", "잔고를 공유" in j)
banner = root.pack_slaves()[0]
check("배너가 화면 맨 위", "ETH" in banner.winfo_children()[0].cget("text"),
      banner.winfo_children()[0].cget("text")[:24])

section("D. 매매 설정은 본인용 BTC 빌드와 같은가")
check("레버리지 입력칸 없음", "레버리지 (1~100배):" not in j)
check("포지션 입력칸 없음", "포지션 크기 (%):" not in j)
check("고정 안내 표시", f"{dist.FIXED_LEVERAGE}배" in j and f"{dist.FIXED_POS_PCT*100:g}%" in j)
check("텔레그램 칸 있음", "텔레그램 알림" in j)
check("대화방 ID 찾기 버튼", "대화방 ID 찾기" in j)
check("야간 정지 기본 꺼짐", app.quiet_on_var.get() is False)

core.LEVERAGE, core.INITIAL_MARGIN_PCT, core.MAX_STEPS = 1, 0.99, 4
core.MINIMAL_LOG, core.SHOW_QTY_DETAIL, core.HEDGE_AT_STEP = False, True, 3
core.STOP_LOSS_COOLDOWN_SEC, core.STOP_LOSS_REST_BOTH_SIDES = 1, False
core.TELEGRAM_BOT_TOKEN = core.TELEGRAM_CHAT_ID = ""

app.tg_token_var.set("123456:ETHTOKEN")
app.tg_chat_var.set("-1009999999999")
app.exchange_var.set("Gate.io")
app._on_exchange_changed(None)
app.api_key_var.set("EK")
app.api_secret_var.set("ES")

seen = {}


def fake_run(self, *a, **k):
    seen["tg"] = (core.TELEGRAM_BOT_TOKEN, core.TELEGRAM_CHAT_ID)
    seen["fixed"] = (core.LEVERAGE, core.INITIAL_MARGIN_PCT, core.MAX_STEPS)
    seen["log"] = (core.MINIMAL_LOG, core.SHOW_QTY_DETAIL, core.HEDGE_AT_STEP)
    seen["rest"] = (core.STOP_LOSS_COOLDOWN_SEC, core.STOP_LOSS_REST_BOTH_SIDES)
    seen["quiet"] = core.QUIET_START_HOUR
    seen["symbol"] = core.EXCHANGE_SYMBOLS.get("Gate.io")
    seen["state"] = core.STATE_PATH


dist.HedgedMartingaleGUI._run_bot = fake_run
POPUPS.clear()
app._on_start_clicked()
for _ in range(60):
    if "fixed" in seen:
        break
    time.sleep(0.05)

check("시작 시 오류창 없음", not POPUPS, str(POPUPS))
eq("레버리지 25배", seen.get("fixed", (0,))[0], dist.FIXED_LEVERAGE)
eq("진입금액 3%", seen.get("fixed", (0, 0))[1], dist.FIXED_POS_PCT)
eq("최대 3차", seen.get("fixed", (0, 0, 0))[2], dist.FIXED_MAX_STEPS)
eq("최소 로그 켜짐", seen.get("log", (None,))[0], True)
eq("수량 숨김", seen.get("log", (0, None))[1], False)
eq("헷지 꺼짐", seen.get("log", (0, 0, None))[2], 0)
eq("손절 휴식 1시간", seen.get("rest", (None,))[0], dist.FIXED_SL_REST_SEC)
eq("손절 휴식 양방향", seen.get("rest", (0, None))[1], True)
eq("야간 정지 꺼짐", seen.get("quiet"), -1)
eq("시작 시점에도 심볼 ETH", seen.get("symbol"), "ETH/USDT:USDT")
check("시작 시점에도 ETH 상태파일", "eth" in str(seen.get("state", "")).lower(),
      str(seen.get("state")))
app._set_stopped_ui()

section("E. 저장 / 복원이 ETH 파일로 간다")
app._save_credentials()
check("ETH 설정 파일이 생성됨", os.path.exists(dist.CONFIG_PATH))
check("BTC 설정 파일은 안 만들어짐", not os.path.exists(btc_cfg))
saved = json.load(open(dist.CONFIG_PATH, encoding="utf-8"))
check("ETH 파일에 토큰 저장", saved.get("tg_token") == "123456:ETHTOKEN")
check("ETH 파일에 API 키 저장",
      (saved.get("exchanges") or {}).get("Gate.io", {}).get("api_key") == "EK")

app2 = eth.EthBotGUI(tk.Toplevel(root))
eq("재시작 후 토큰 복원", app2.tg_token_var.get(), "123456:ETHTOKEN")
eq("재시작 후 API 키 복원", app2.api_key_var.get(), "EK")

section("F. 붙여넣기·창 크기는 그대로 동작")
entries = []


def find(w):
    for c in w.winfo_children():
        if isinstance(c, tk.Entry):
            entries.append(c)
        find(c)


find(root)
check("모든 입력칸에 Ctrl 바인딩", all("<Control-Key>" in e.bind() for e in entries),
      f"{len(entries)}개")
check("모든 입력칸에 오른쪽 클릭", all("<Button-3>" in e.bind() for e in entries))
chat = [e for e in entries if e.cget("textvariable") == str(app.tg_chat_var)][0]
root.clipboard_clear()
root.clipboard_append("-1001111111111")
chat.delete(0, "end")
chat.focus_force()
root.update()
chat.event_generate("<Control-KeyPress>", keysym="Hangul", keycode=86)
root.update()
eq("한글 상태 붙여넣기", app.tg_chat_var.get(), "-1001111111111")
mw, mh = root.minsize()
check("세로 최소크기가 화면 안", mh <= root.winfo_screenheight() * 0.88, f"{mh}")

section("G. 다른 빌드 파일을 건드리지 않았는가")
for f, label in (("hedged_martingale_bot_gui.py", "배포용"),
                 ("hedged_martingale_bot_my.py", "본인용 BTC")):
    src = open(os.path.join(R, f), encoding="utf-8").read()
    check(f"{label}에 ETH 하드코딩 없음", "ETH/USDT" not in src)
eth_src = open(os.path.join(R, "hedged_martingale_bot_eth.py"), encoding="utf-8").read()
check("ETH 빌드는 본인용을 상속만 함", "class EthBotGUI(my.MyBotGUI)" in eth_src)
for label, sec in (("게이트 Key", "68b5d63d0b692801f43fba329bad52f3"),
                   ("게이트 Secret", "ae611a7f687583cf7d90810e284d2f15cea1d2a8a4d22937aba1bd27e9408eed"),
                   ("텔레그램 토큰", "8715993070:AAF24cb1k_jR-pZIxQID_kFsCOnMxk2cTC0")):
    check(f"ETH 파일에 {label} 없음", sec not in eth_src)

root.destroy()
print()
sys.exit(0 if report("검증 15 — 이더리움 전용 빌드") else 1)
