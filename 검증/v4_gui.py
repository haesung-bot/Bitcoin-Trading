# -*- coding: utf-8 -*-
"""최종 검증 4 — 배포용/개인용 GUI."""
import sys, os, json, logging, tempfile, threading
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from v_common import *

import tkinter as tk
from tkinter import messagebox
import hedged_martingale_bot as core

HOME = tempfile.mkdtemp()
os.environ["HOME"] = HOME
os.path.expanduser = lambda p: p.replace("~", HOME) if p.startswith("~") else p

import hedged_martingale_bot_gui as dist
import martingale_bot_mine as mine

core.logger.setLevel(logging.CRITICAL)

POPUPS = []
for nm in ("showerror", "showinfo", "showwarning"):
    def mkp(kind):
        def f(title, msg, *a, **k):
            POPUPS.append((kind, title, msg))
        return f
    for mod in (messagebox, dist.messagebox, mine.messagebox):
        setattr(mod, nm, mkp(nm))
ASK = {"v": False}
for mod in (messagebox, dist.messagebox, mine.messagebox):
    mod.askyesno = lambda *a, **k: ASK["v"]


def build(mod, cls):
    root = tk.Tk()
    app = getattr(mod, cls)(root)
    root.update_idletasks()
    return root, app


# ══════════════════════════════════════════════════════════
section("21. 두 GUI 생성 / 구성 요소")

for mod, cls, label in ((dist, "HedgedMartingaleGUI", "배포용"), (mine, "PersonalTradingGUI", "개인용")):
    root, app = build(mod, cls)
    check(f"{label}: 창 생성", root.winfo_exists() == 1)
    check(f"{label}: 제목", bool(root.title()), root.title())
    for attr in ("exchange_var", "api_key_var", "api_secret_var", "passphrase_var",
                 "leverage_var", "pos_pct_var", "log_box", "status_var", "start_btn", "stop_btn"):
        check(f"{label}: {attr}", hasattr(app, attr))
    check(f"{label}: 시작버튼 활성", str(app.start_btn["state"]) == "normal")
    check(f"{label}: 정지버튼 비활성", str(app.stop_btn["state"]) == "disabled")
    try:
        if getattr(app, "_status_after_id", None):
            root.after_cancel(app._status_after_id)
    except Exception:
        pass
    root.update()
    root.destroy()

root_d, app_d = build(dist, "HedgedMartingaleGUI")
root_m, app_m = build(mine, "PersonalTradingGUI")
check("개인용: 텔레그램 칸", hasattr(app_m, "tg_token_var") and hasattr(app_m, "tg_chat_var"))
check("개인용: 익절/물타기/손절 칸", all(hasattr(app_m, a) for a in ("tp_pct_var", "step_pct_var", "sl_pct_var")))
check("개인용: 추세필터 체크박스", hasattr(app_m, "trend_var"))
check("개인용: 3차헷지 체크박스", hasattr(app_m, "hedge_var"))
check("개인용: 스크롤 캔버스", hasattr(app_m, "canvas") and hasattr(app_m, "vscroll"))
check("배포용: 텔레그램 칸 없음", not hasattr(app_d, "tg_token_var"))
check("배포용: 헷지/추세 옵션 없음", not hasattr(app_d, "hedge_var") and not hasattr(app_d, "trend_var"))

# ══════════════════════════════════════════════════════════
section("22. 파일 경로 분리")

check("설정 파일 분리", dist.CONFIG_PATH != mine.CONFIG_PATH,
      f"{os.path.basename(dist.CONFIG_PATH)} vs {os.path.basename(mine.CONFIG_PATH)}")
check("상태 파일 개인용 전용", "mine" in mine.core.STATE_PATH)
check("기록 파일 개인용 전용", "mine" in mine.core.TRADE_LOG_PATH)

# ══════════════════════════════════════════════════════════
section("23. 설정 저장 / 복원")

CREDS = {"Gate.io": ("GK", "GS", ""), "Binance": ("BK", "BS", ""), "OKX": ("OK", "OS", "OP")}
for name, (k, s, p) in CREDS.items():
    app_m.exchange_var.set(name)
    app_m._on_exchange_changed(None)
    app_m.api_key_var.set(k)
    app_m.api_secret_var.set(s)
    app_m.passphrase_var.set(p)
    app_m._save_credentials(name)
for name, (k, s, p) in CREDS.items():
    app_m.api_key_var.set("")
    app_m.api_secret_var.set("")
    app_m.passphrase_var.set("")
    app_m._load_saved_credentials(name)
    check(f"{name}: Key 복원", app_m.api_key_var.get() == k)
    check(f"{name}: Secret 복원", app_m.api_secret_var.get() == s)
    if p:
        check(f"{name}: Passphrase 복원", app_m.passphrase_var.get() == p)

app_m.exchange_var.set("Gate.io")
app_m._on_exchange_changed(None)
app_m.api_key_var.set("NEWKEY")
app_m._schedule_save()
app_m.exchange_var.set("Binance")
app_m._on_exchange_changed(None)
app_m.exchange_var.set("Gate.io")
app_m._on_exchange_changed(None)
check("거래소 빠른 전환 시 키 유지", app_m.api_key_var.get() == "NEWKEY", app_m.api_key_var.get())

TOKEN = "8715993070:TESTTOKEN_abcdef"
CHAT = "66721231, -1004339079566"
app_m.tg_token_var.set(TOKEN)
app_m.tg_chat_var.set(CHAT)
app_m.leverage_var.set("25")
app_m.pos_pct_var.set("3")
app_m.tp_pct_var.set("0.35")
app_m.step_pct_var.set("1.4")
app_m.sl_pct_var.set("2.8")
app_m.trend_var.set(True)
app_m.hedge_var.set(True)
app_m.verbose_var.set(False)
app_m._flush_pending_save()
app_m._save_credentials()
root_m.destroy()

root_m, app_m = build(mine, "PersonalTradingGUI")
eq("재시작: 텔레그램 토큰", app_m.tg_token_var.get(), TOKEN)
eq("재시작: Chat ID", app_m.tg_chat_var.get(), CHAT)
eq("재시작: 레버리지", app_m.leverage_var.get(), "25")
eq("재시작: 포지션%", app_m.pos_pct_var.get(), "3")
eq("재시작: 익절%", app_m.tp_pct_var.get(), "0.35")
eq("재시작: 물타기%", app_m.step_pct_var.get(), "1.4")
eq("재시작: 손절%", app_m.sl_pct_var.get(), "2.8")
eq("재시작: 추세필터", app_m.trend_var.get(), True)
eq("재시작: 3차헷지", app_m.hedge_var.get(), True)
eq("재시작: 상세로그", app_m.verbose_var.get(), False)

# ══════════════════════════════════════════════════════════
section("24. 시작 버튼 입력 검증")

for app, label in ((app_d, "배포용"), (app_m, "개인용")):
    app.exchange_var.set("Gate.io")
    app._on_exchange_changed(None)
    POPUPS.clear()
    app.api_key_var.set("")
    app.api_secret_var.set("")
    app._on_start_clicked()
    check(f"{label}: 키 없이 시작 거부", any(k == "showerror" for k, _, _ in POPUPS))
    check(f"{label}: 스레드 안 뜸", app.worker_thread is None or not app.worker_thread.is_alive())
    POPUPS.clear()
    app.exchange_var.set("OKX")
    app._on_exchange_changed(None)
    app.api_key_var.set("K")
    app.api_secret_var.set("S")
    app.passphrase_var.set("")
    app._on_start_clicked()
    check(f"{label}: OKX Passphrase 누락 거부", any("Passphrase" in m for _, _, m in POPUPS))
    app.exchange_var.set("Gate.io")
    app._on_exchange_changed(None)
    app.api_key_var.set("K")
    app.api_secret_var.set("S")
    if label == "배포용":
        texts = []
        def walk(w):
            for c in w.winfo_children():
                try:
                    t = c.cget("text")
                except Exception:
                    t = ""
                if t:
                    texts.append(str(t))
                walk(c)
        walk(app.root)
        joined = " ".join(texts)
        check("배포용: 레버리지 입력칸 없음", "레버리지 (1~100배):" not in joined)
        check("배포용: 포지션 입력칸 없음", "포지션 크기 (%):" not in joined)
        check("배포용: 고정 안내 표시",
              f"{dist.FIXED_LEVERAGE}배" in joined and f"{dist.FIXED_POS_PCT*100:g}%" in joined,
              joined[:80])
        POPUPS.clear()
        core.LEVERAGE, core.INITIAL_MARGIN_PCT, core.MAX_STEPS = 1, 0.99, 4
        core.MINIMAL_LOG, core.SHOW_QTY_DETAIL, core.HEDGE_AT_STEP = False, True, 3
        orig = app._run_bot
        app._run_bot = lambda *a, **k: None
        app._on_start_clicked()
        app._run_bot = orig
        check("배포용: 시작 시 오류창 없음", not any(k == "showerror" for k, _, _ in POPUPS), str(POPUPS))
        eq("배포용: 레버리지 고정", core.LEVERAGE, dist.FIXED_LEVERAGE)
        eq("배포용: 포지션 크기 고정", core.INITIAL_MARGIN_PCT, dist.FIXED_POS_PCT)
        eq("배포용: 최대 단계 고정", core.MAX_STEPS, dist.FIXED_MAX_STEPS)
        eq("배포용: 최소 로그 켜짐", core.MINIMAL_LOG, True)
        eq("배포용: 수량 숨김", core.SHOW_QTY_DETAIL, False)
        eq("배포용: 헷지 꺼짐", core.HEDGE_AT_STEP, 0)
        check("배포용: 증거금이 잔고를 넘지 않음",
              dist.FIXED_POS_PCT * (2 ** dist.FIXED_MAX_STEPS - 1) <= 1.0,
              f"{dist.FIXED_POS_PCT*(2**dist.FIXED_MAX_STEPS-1)*100:.0f}%")
        app._set_stopped_ui()
    else:
        for bad in ("0", "101", "abc", "-5", ""):
            POPUPS.clear()
            app.leverage_var.set(bad)
            app._on_start_clicked()
            check(f"{label}: 레버리지 '{bad}' 거부", any(k == "showerror" for k, _, _ in POPUPS))
        app.leverage_var.set("20")
        for bad, why in (("0", "0%"), ("-1", "음수"), ("abc", "글자"), ("7", "4차시 105%")):
            POPUPS.clear()
            app.pos_pct_var.set(bad)
            app._on_start_clicked()
            check(f"{label}: 포지션 '{bad}' 거부({why})", any(k == "showerror" for k, _, _ in POPUPS))
        for good in ("2", "6.6"):
            POPUPS.clear()
            app.pos_pct_var.set(good)
            orig = app._run_bot
            app._run_bot = lambda *a, **k: None
            app._on_start_clicked()
            app._run_bot = orig
            check(f"{label}: 포지션 '{good}' 허용", not any(k == "showerror" for k, _, _ in POPUPS), str(POPUPS))
            app._set_stopped_ui()
    app.pos_pct_var.set("2")

app_m.api_key_var.set("K")
app_m.api_secret_var.set("S")
for attr, bad in (("tp_pct_var", "0"), ("tp_pct_var", "abc"), ("step_pct_var", "-1"), ("sl_pct_var", "0")):
    POPUPS.clear()
    old = getattr(app_m, attr).get()
    getattr(app_m, attr).set(bad)
    app_m._on_start_clicked()
    check(f"개인용: {attr}='{bad}' 거부", any(k == "showerror" for k, _, _ in POPUPS))
    getattr(app_m, attr).set(old)

# 옵션이 엔진에 전달되는지
core.TREND_EMA_PERIOD = 0
core.HEDGE_AT_STEP = 0
app_m.trend_var.set(True)
app_m.hedge_var.set(True)
app_m.leverage_var.set("20")
app_m.pos_pct_var.set("2")
captured = {}
orig = app_m._run_bot
app_m._run_bot = lambda *a, **k: captured.update(trend=core.TREND_EMA_PERIOD, hedge=core.HEDGE_AT_STEP,
                                                 tok=core.TELEGRAM_BOT_TOKEN, chat=core.TELEGRAM_CHAT_ID)
POPUPS.clear()
app_m._on_start_clicked()
app_m._run_bot = orig
app_m._set_stopped_ui()
eq("추세필터 ON → EMA200 전달", captured.get("trend"), 200)
eq("3차헷지 ON → HEDGE_AT_STEP=3", captured.get("hedge"), 3)
eq("텔레그램 토큰 전달", captured.get("tok"), TOKEN)
eq("Chat ID 전달", captured.get("chat"), CHAT)
core.TREND_EMA_PERIOD = 0
core.HEDGE_AT_STEP = 0

# ══════════════════════════════════════════════════════════
section("25. API 키 노출 / 마스킹")

SK, SS, SP2 = "SECRET_KEY_abc123", "SECRET_SEC_xyz789", "SECRET_PASS_999"
for app, label in ((app_d, "배포용"), (app_m, "개인용")):
    app.exchange_var.set("OKX")
    app._on_exchange_changed(None)
    app.api_key_var.set(SK)
    app.api_secret_var.set(SS)
    app.passphrase_var.set(SP2)
    app.leverage_var.set("10")
    app.pos_pct_var.set("2")
    orig = app._run_bot
    app._run_bot = lambda *a, **k: None
    POPUPS.clear()
    app._on_start_clicked()
    app._run_bot = orig
    app.root.update()
    txt = app.log_box.get("1.0", "end")
    check(f"{label}: API Key 로그 미노출", SK not in txt)
    check(f"{label}: Secret 로그 미노출", SS not in txt)
    check(f"{label}: Passphrase 로그 미노출", SP2 not in txt)
    app._set_stopped_ui()
    masked = []

    def walk(w):
        for c in w.winfo_children():
            if isinstance(c, tk.Entry) and str(c.cget("show")) == "*":
                masked.append(1)
            walk(c)
    walk(app.root)
    check(f"{label}: 비밀 입력칸 마스킹", len(masked) >= 2, f"{len(masked)}개")

# ══════════════════════════════════════════════════════════
section("26. 상태창 / 로그 / 하트비트")

from hedged_martingale_bot import Fill, Side
core.LEVERAGE, core.INITIAL_MARGIN_PCT = 50, 0.02


class FM:
    def __init__(self, side, fills, step):
        self.side, self.fills, self.step = side, fills, step
        self.total_qty = sum(f.qty for f in fills)
        self.avg_price = sum(f.price * f.qty for f in fills) / self.total_qty
        self.in_position = True


class FB:
    long = FM(Side.LONG, [Fill(63785.30, 0.0257)], 1)
    short = FM(Side.SHORT, [Fill(63818.0, 0.0258), Fill(64583.82, 0.0516)], 2)


app_m.bot = FB()
app_m.exchange_label = "Gate.io"
app_m._update_position_status()
t = app_m.status_var.get()
check("개인용 상태창: BTC 수량 미표시", "BTC" not in t, t[:60])
check("개인용 상태창: USDT 표시", "USDT" in t)
check("개인용 상태창: 진입 차수", "1차 진입 중" in t and "2차 진입 중" in t)
check("개인용 상태창: 1~4차 전부", all(f"{i}차" in t for i in (1, 2, 3, 4)))
check("개인용 상태창: 체결 표시 ✓", "✓1차" in t)
app_d.bot = FB()
app_d.exchange_label = "Gate.io"
app_d._update_position_status()
check("배포용 상태창: 차수 미노출", "차 진입 중" not in app_d.status_var.get())

for app, label in ((app_d, "배포용"), (app_m, "개인용")):
    app.log_box.configure(state="normal")
    app.log_box.delete("1.0", "end")
    app.log_box.configure(state="disabled")
    app._last_line_is_heartbeat = False
    for i in range(60):
        app._append_log(f"12:00:{i%60:02d} {core.HEARTBEAT_MARK} 시세 65,{i:03d}.00 | 롱 대기 | 숏 대기")
    body = app.log_box.get("1.0", "end")
    eq(f"{label}: 하트비트 60회 → 1줄", body.count(core.HEARTBEAT_MARK), 1)
    app._append_log("12:01:00 ▶ 숏 진입 | 금액 965.37 USDT")
    for i in range(60):
        app._append_log(f"12:01:{i%60:02d} {core.HEARTBEAT_MARK} 시세 65,{i:03d}.00 | 롱 대기 | 숏 1차")
    body = app.log_box.get("1.0", "end")
    check(f"{label}: 진입 기록 보존", "숏 진입" in body)
    real = [l for l in body.strip().split("\n") if l.strip()]
    eq(f"{label}: 총 3줄로 접힘", len(real), 3)
    for i in range(1500):
        app._append_log(f"라인 {i}")
    lines = int(app.log_box.index("end-1c").split(".")[0])
    check(f"{label}: 로그 줄수 상한", lines <= 600, f"{lines}줄")

# ══════════════════════════════════════════════════════════
section("27. 매매 기록 / 안내창 / 스레드 안전")

for app, label in ((app_d, "배포용"), (app_m, "개인용")):
    app.trade_history = []
    for i in range(3):
        app._on_trade_closed({"time": f"2026-08-21 10:0{i}:00", "side": "LONG", "reason": "익절",
                              "entry_price": 60000.0 + i, "exit_price": 60180.0 + i,
                              "qty": 0.0159, "profit_usdt": 5.0 + i, "leveraged_return_pct": 3.0})
    app.root.update()
    eq(f"{label}: 기록 3건", len(app.trade_history), 3)
    app._save_trade_history()
    app.trade_history = []
    app._load_trade_history()
    eq(f"{label}: 파일에서 복원", len(app.trade_history), 3)
    app._show_trade_history()
    app.root.update()
    tops = [w for w in app.root.winfo_children() if isinstance(w, tk.Toplevel)]
    check(f"{label}: 기록 창 열림", len(tops) >= 1)
    for w in tops:
        w.destroy()
    for name in core.EXCHANGE_OPTIONS:
        app.exchange_var.set(name)
        app._on_exchange_changed(None)
        app._show_api_guide()
        app.root.update()
        tops = [w for w in app.root.winfo_children() if isinstance(w, tk.Toplevel)]
        check(f"{label}: {name} 가이드 창", len(tops) >= 1)
        for w in tops:
            w.destroy()

app_d._show_caution()
app_d.root.update()
check("배포용: 주의사항 창", any(isinstance(w, tk.Toplevel) for w in app_d.root.winfo_children()))
for w in app_d.root.winfo_children():
    if isinstance(w, tk.Toplevel):
        w.destroy()

for app, label in ((app_d, "배포용"), (app_m, "개인용")):
    errs = []

    def wk(a=app):
        try:
            for i in range(200):
                a._log(f"워커 {i}")
                a._run_on_ui(lambda: a.status_var.set("워커 갱신"))
        except Exception as e:
            errs.append(e)
    ths = [threading.Thread(target=wk) for _ in range(3)]
    for t2 in ths:
        t2.start()
    for t2 in ths:
        t2.join()
    for _ in range(30):
        app.root.update()
        app._poll_log_queue()
    check(f"{label}: 워커 스레드 UI 갱신 안전", not errs, str(errs[:1]))
    eq(f"{label}: 상태창 갱신됨", app.status_var.get(), "워커 갱신")
    app._set_stopped_ui()
    check(f"{label}: 정지 후 버튼 복구",
          str(app.start_btn["state"]) == "normal" and str(app.stop_btn["state"]) == "disabled")

# ══════════════════════════════════════════════════════════
section("28. 개인용 창 크기 / 스크롤")

ratio = app_m.root.winfo_height() / app_m.content.winfo_reqheight()
check("창 높이 = 내용의 약 70%", 0.55 < ratio < 0.85, f"{ratio:.2f}")


class W:
    def __init__(self, w, num=0, delta=-120):
        self.widget, self.num, self.delta = w, num, delta


b0 = app_m.canvas.yview()[0]
app_m._on_mousewheel(W(app_m.log_box))
app_m.root.update_idletasks()
eq("로그창 위 휠 → 전체 스크롤 안 함", app_m.canvas.yview()[0], b0)
app_m._on_mousewheel(W(app_m.canvas))
app_m.root.update_idletasks()
check("캔버스 휠 → 전체 스크롤", app_m.canvas.yview()[0] > b0)
ok = True
try:
    app_m._on_mousewheel(W(app_m.canvas, num=4))
    app_m._on_mousewheel(W(app_m.canvas, num=5))
except Exception:
    ok = False
check("리눅스 휠 이벤트 안전", ok)
for name in core.EXCHANGE_OPTIONS:
    app_m.exchange_var.set(name)
    app_m._on_exchange_changed(None)
    app_m.root.update_idletasks()
    r = app_m.root.winfo_height() / app_m.content.winfo_reqheight()
    check(f"{name} 전환 후 비율 유지", 0.55 < r < 0.85, f"{r:.2f}")

root_d.destroy()
root_m.destroy()

print()
sys.exit(0 if report("검증 4 — GUI") else 1)
