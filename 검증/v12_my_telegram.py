# -*- coding: utf-8 -*-
"""검증 12 — 운영자 본인용 빌드(hedged_martingale_bot_my.py).

배포용에 텔레그램 알림만 얹은 것이므로, 확인할 것은 두 가지다.
  1) 텔레그램 입력칸이 제대로 붙고 저장·복원·전달되는가
  2) 그 외 모든 것(고정 설정, 최소 로그)이 배포용과 똑같은가
그리고 배포용 파일 자체에는 텔레그램이 새어 들어가지 않아야 한다.
"""
import sys, os, time, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from v_common import *

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, R)

# 실제 설정 파일을 건드리지 않도록 홈 디렉터리를 임시로 돌린다.
os.environ["HOME"] = tempfile.mkdtemp(prefix="mybot-home-")

import tkinter as tk
from tkinter import messagebox
import hedged_martingale_bot as core
import hedged_martingale_bot_gui as dist
import hedged_martingale_bot_my as my

POPUPS = []
for name in ("showerror", "showinfo", "showwarning"):
    setattr(messagebox, name, lambda *a, **k: POPUPS.append(("popup", a, k)))
messagebox.askyesno = lambda *a, **k: False

root = tk.Tk()
app = my.MyBotGUI(root)
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


section("A. 텔레그램 입력칸이 붙었는가")
j = all_text(root)
check("텔레그램 프레임 있음", "텔레그램 알림" in j)
check("대화방 ID 찾기 버튼", "대화방 ID 찾기" in j)
check("연결 테스트 버튼", "연결 테스트" in j)
check("그룹방 안내 문구", "-100" in j)
check("토큰 입력칸 가려짐(show='*')", any(
    isinstance(c, tk.Entry) and c.cget("show") == "*"
    for f in root.winfo_children() for c in f.winfo_children()
    if isinstance(f, tk.LabelFrame) and "텔레그램" in str(f.cget("text"))
))

order = [w.cget("text").strip() if isinstance(w, tk.LabelFrame) else "" for w in root.pack_slaves()]
idx_cfg = order.index("매매 설정")
idx_tg = order.index("텔레그램 알림")
idx_log = order.index("실시간 매매 로그 및 상태")
check("매매 설정 아래에 배치", idx_cfg < idx_tg, f"{idx_cfg} < {idx_tg}")
check("로그창 위에 배치", idx_tg < idx_log, f"{idx_tg} < {idx_log}")

section("A-2. 입력칸 붙여넣기 / 오른쪽 클릭")


def find_entries(w, out=None):
    out = [] if out is None else out
    for c in w.winfo_children():
        if isinstance(c, tk.Entry):
            out.append(c)
        find_entries(c, out)
    return out


all_entries = find_entries(root)
check("모든 입력칸에 Ctrl 바인딩", all(
    "<Control-Key>" in e.bind() for e in all_entries), f"{len(all_entries)}개")
check("모든 입력칸에 오른쪽 클릭 바인딩", all(
    "<Button-3>" in e.bind() for e in all_entries))

chat = [e for e in all_entries if e.cget("textvariable") == str(app.tg_chat_var)][0]
root.clipboard_clear()
root.clipboard_append("-1001234567890")
chat.delete(0, "end")
chat.focus_force()
root.update()
chat.event_generate("<Control-KeyPress>", keysym="v", keycode=86)
root.update()
eq("Ctrl+V(영문) 붙여넣기", app.tg_chat_var.get(), "-1001234567890")

chat.delete(0, "end")
root.update()
# 한글 입력 상태에서는 keysym이 'v'로 오지 않는다. keycode로도 처리돼야 한다.
chat.event_generate("<Control-KeyPress>", keysym="Hangul", keycode=86)
root.update()
eq("Ctrl+V(한글 상태) 붙여넣기", app.tg_chat_var.get(), "-1001234567890")

chat.delete(0, "end")
root.update()
chat.event_generate("<Control-KeyPress>", keysym="v", keycode=86)
root.update()
eq("두 번 붙지 않음", app.tg_chat_var.get(), "-1001234567890")

chat.delete(0, "end")
for ch in "555":
    chat.event_generate("<KeyPress>", keysym=ch)
root.update()
eq("직접 타이핑", app.tg_chat_var.get(), "555")

root.clipboard_clear()
root.clipboard_append("777")
chat.event_generate("<Control-KeyPress>", keysym="a", keycode=65)
root.update()
chat.event_generate("<Control-KeyPress>", keysym="v", keycode=86)
root.update()
eq("전체선택 후 덮어쓰기", app.tg_chat_var.get(), "777")

chat.delete(0, "end")
root.update()

section("A-3. 창 크기가 화면을 넘지 않는가")
mw, mh = root.minsize()
check("세로 최소크기가 화면보다 작음", mh <= root.winfo_screenheight() * 0.88, f"minsize 세로 {mh}")
check("창 높이가 화면 안", root.winfo_height() <= root.winfo_screenheight() * 0.9,
      f"{root.winfo_height()} / 화면 {root.winfo_screenheight()}")

section("B. 배포용과 똑같이 고정돼 있는가")
check("레버리지 입력칸 없음", "레버리지 (1~100배):" not in j)
check("포지션 입력칸 없음", "포지션 크기 (%):" not in j)
check("고정 안내 표시", f"{dist.FIXED_LEVERAGE}배" in j and f"{dist.FIXED_POS_PCT*100:g}%" in j)

core.LEVERAGE, core.INITIAL_MARGIN_PCT, core.MAX_STEPS = 1, 0.99, 4
core.MINIMAL_LOG, core.SHOW_QTY_DETAIL, core.HEDGE_AT_STEP = False, True, 3
core.TELEGRAM_BOT_TOKEN = core.TELEGRAM_CHAT_ID = ""

app.tg_token_var.set("123456:TESTTOKEN")
app.tg_chat_var.set("-1001234567890, 555")
app.exchange_var.set("Gate.io")
app._on_exchange_changed(None)
app.api_key_var.set("K")
app.api_secret_var.set("S")

seen = {}


def fake_run(self, *a, **k):
    seen["tg"] = (core.TELEGRAM_BOT_TOKEN, core.TELEGRAM_CHAT_ID)
    seen["fixed"] = (core.LEVERAGE, core.INITIAL_MARGIN_PCT, core.MAX_STEPS)
    seen["log"] = (core.MINIMAL_LOG, core.SHOW_QTY_DETAIL, core.HEDGE_AT_STEP)


dist.HedgedMartingaleGUI._run_bot = fake_run
POPUPS.clear()
app._on_start_clicked()
for _ in range(60):
    if "tg" in seen:
        break
    time.sleep(0.05)

check("시작 시 오류창 없음", not POPUPS, str(POPUPS))
eq("레버리지 고정 그대로", seen.get("fixed", (0,))[0], dist.FIXED_LEVERAGE)
eq("진입금액 고정 그대로", seen.get("fixed", (0, 0))[1], dist.FIXED_POS_PCT)
eq("최대 단계 고정 그대로", seen.get("fixed", (0, 0, 0))[2], dist.FIXED_MAX_STEPS)
eq("최소 로그 그대로", seen.get("log", (None,))[0], True)
eq("수량 숨김 그대로", seen.get("log", (0, None))[1], False)
eq("헷지 꺼짐 그대로", seen.get("log", (0, 0, None))[2], 0)

section("C. 텔레그램 값이 매매 스레드까지 전달되는가")
eq("봇 토큰 전달", seen.get("tg", ("", ""))[0], "123456:TESTTOKEN")
eq("Chat ID 전달", seen.get("tg", ("", ""))[1], "-1001234567890, 555")
check("Chat ID 2곳으로 분해", core.parse_chat_ids(seen.get("tg", ("", ""))[1]) ==
      ["-1001234567890", "555"], str(core.parse_chat_ids(seen.get("tg", ("", ""))[1])))

app._set_stopped_ui()

section("D. 저장 / 복원")
app._save_credentials()
saved = json.load(open(dist.CONFIG_PATH, encoding="utf-8"))
check("설정 파일에 토큰 저장", saved.get("tg_token") == "123456:TESTTOKEN")
check("설정 파일에 Chat ID 저장", saved.get("tg_chat") == "-1001234567890, 555")
check("API 키 저장도 그대로", (saved.get("exchanges") or {}).get("Gate.io", {}).get("api_key") == "K")

app2 = my.MyBotGUI(tk.Toplevel(root))
eq("재시작 후 토큰 복원", app2.tg_token_var.get(), "123456:TESTTOKEN")
eq("재시작 후 Chat ID 복원", app2.tg_chat_var.get(), "-1001234567890, 555")
app2.exchange_var.set("Binance")
app2._on_exchange_changed(None)
eq("거래소 바꿔도 토큰 유지", app2.tg_token_var.get(), "123456:TESTTOKEN")

section("E. 토큰 없이도 동작하는가")
app.tg_token_var.set("")
app.tg_chat_var.set("")
seen.clear()
POPUPS.clear()
app.api_key_var.set("K")
app.api_secret_var.set("S")
app.exchange_var.set("Gate.io")
app._on_exchange_changed(None)
app._on_start_clicked()
for _ in range(60):
    if "tg" in seen:
        break
    time.sleep(0.05)
check("텔레그램 비워도 시작됨", "tg" in seen and not POPUPS, str(POPUPS))
eq("빈 토큰이 그대로 전달", seen.get("tg", (None,))[0], "")
app._set_stopped_ui()

section("F. 배포용 파일은 오염되지 않았는가")
dist_src = open(os.path.join(R, "hedged_martingale_bot_gui.py"), encoding="utf-8").read()
check("배포용에 텔레그램 입력칸 없음", "tg_token_var" not in dist_src)
check("배포용에 대화방 찾기 없음", "_discover_chats" not in dist_src)
check("배포용에 requests import 없음", "\nimport requests" not in dist_src)
my_src = open(os.path.join(R, "hedged_martingale_bot_my.py"), encoding="utf-8").read()
for label, sec in (("게이트 Key", "68b5d63d0b692801f43fba329bad52f3"),
                   ("게이트 Secret", "ae611a7f687583cf7d90810e284d2f15cea1d2a8a4d22937aba1bd27e9408eed"),
                   ("텔레그램 토큰", "8715993070:AAF24cb1k_jR-pZIxQID_kFsCOnMxk2cTC0")):
    check(f"본인용 파일에 {label} 없음", sec not in my_src)
check("본인용은 배포용을 상속만 함", "class MyBotGUI(dist.HedgedMartingaleGUI)" in my_src)

root.destroy()
print()
sys.exit(0 if report("검증 12 — 본인용 텔레그램 빌드") else 1)
