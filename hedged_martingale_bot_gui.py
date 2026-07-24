"""
hedged_martingale_bot_gui.py
비트코인 10배 레버리지 양방향 마틴게일 자동매매 - Gate.io 실거래 GUI

hedged_martingale_bot.py의 전략 엔진을 그대로 사용한다. API 키를 입력하고
'매매 시작' 버튼을 누르면 실제 Gate.io 계좌로 주문이 나가는 실거래가 시작된다
(모의매매 아님). 전략 파라미터(레버리지, 마진 비율, 진입/청산 조건 등)는 화면에
표시만 되고 수정할 수 없다 — 값을 바꾸려면 hedged_martingale_bot.py 상단의
설정값을 직접 수정해야 한다.

exe로 빌드하는 방법은 hedged_martingale_bot_exe_빌드_방법.md 참고.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext

import hedged_martingale_bot as core

CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".hedged_martingale_bot_gui_config.json")


class QueueLogHandler(logging.Handler):
    def __init__(self, log_queue: queue.Queue[str]):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record: logging.LogRecord) -> None:
        self.log_queue.put(self.format(record))


class HedgedMartingaleGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("비트코인 양방향 마틴게일 자동매매 (Gate.io 실거래)")
        self.root.geometry("680x580")
        self.root.minsize(600, 480)

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.stop_event = threading.Event()
        self.worker_thread: threading.Thread | None = None

        self._build_widgets()
        self._load_saved_credentials()
        self._install_log_handler()
        self._poll_log_queue()
        self._log("프로그램이 시작되었습니다. API Key/Secret 입력 후 '매매 시작'을 누르세요.")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ───────────── UI 구성 ─────────────
    def _build_widgets(self) -> None:
        info = (
            f"거래소: Gate.io 선물({core.SYMBOL}) / 레버리지: {core.LEVERAGE}x / "
            f"1차진입 마진: 잔고의 {core.INITIAL_MARGIN_PCT * 100:.0f}%\n"
            f"마틴게일: 평단가 대비 {core.STEP_TRIGGER_PCT * 100:.1f}%마다 1→2→4→8배 추가 진입, "
            f"+{core.TP_PCT * 100:.1f}% 익절\n"
            f"4단계(8배) 후 추가 {core.STEP_TRIGGER_PCT * 100:.1f}% 역행 시 하드손절 / 청산 후 "
            f"{core.COOLDOWN_SEC // 60}분 쿨다운\n"
            f"연속손절 {core.MAX_CONSECUTIVE_SL}회 발생 시 해당 방향(롱/숏) 자동 정지"
        )
        tk.Label(self.root, text=info, justify="left", fg="#333").pack(anchor="w", padx=12, pady=(10, 6))

        form = tk.Frame(self.root)
        form.pack(fill="x", padx=12)

        tk.Label(form, text="Gate.io API Key").grid(row=0, column=0, sticky="w", pady=3)
        self.api_key_var = tk.StringVar()
        tk.Entry(form, textvariable=self.api_key_var, width=50).grid(row=0, column=1, pady=3, sticky="we")

        tk.Label(form, text="Gate.io API Secret").grid(row=1, column=0, sticky="w", pady=3)
        self.api_secret_var = tk.StringVar()
        tk.Entry(form, textvariable=self.api_secret_var, width=50, show="*").grid(row=1, column=1, pady=3, sticky="we")

        tk.Label(form, text="텔레그램 봇 토큰(선택)").grid(row=2, column=0, sticky="w", pady=3)
        self.tg_token_var = tk.StringVar()
        tk.Entry(form, textvariable=self.tg_token_var, width=50).grid(row=2, column=1, pady=3, sticky="we")

        tk.Label(form, text="텔레그램 Chat ID(선택)").grid(row=3, column=0, sticky="w", pady=3)
        self.tg_chat_var = tk.StringVar()
        tk.Entry(form, textvariable=self.tg_chat_var, width=50).grid(row=3, column=1, pady=3, sticky="we")

        form.columnconfigure(1, weight=1)

        btn_frame = tk.Frame(self.root)
        btn_frame.pack(fill="x", padx=12, pady=10)

        self.start_btn = tk.Button(
            btn_frame, text="▶ 매매 시작 (실거래)", bg="#c0392b", fg="white",
            font=("맑은 고딕", 11, "bold"), height=2, command=self._on_start_clicked,
        )
        self.start_btn.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.stop_btn = tk.Button(
            btn_frame, text="■ 정지", bg="#7f8c8d", fg="white",
            font=("맑은 고딕", 11, "bold"), height=2, state="disabled", command=self._on_stop_clicked,
        )
        self.stop_btn.pack(side="left", fill="x", expand=True, padx=(6, 0))

        self.status_var = tk.StringVar(value="상태: 대기 중")
        tk.Label(self.root, textvariable=self.status_var, anchor="w", font=(None, 10, "bold")).pack(fill="x", padx=12)

        self.log_box = scrolledtext.ScrolledText(self.root, height=18, state="disabled", font=("Consolas", 9))
        self.log_box.pack(fill="both", expand=True, padx=12, pady=(6, 12))

    # ───────────── 로그 ─────────────
    def _install_log_handler(self) -> None:
        handler = QueueLogHandler(self.log_queue)
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
        core.logger.addHandler(handler)
        core.logger.setLevel(logging.INFO)

    def _poll_log_queue(self) -> None:
        while not self.log_queue.empty():
            self._append_log(self.log_queue.get_nowait())
        self.root.after(200, self._poll_log_queue)

    def _append_log(self, text: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _log(self, text: str) -> None:
        self.log_queue.put(text)

    # ───────────── 자격증명 저장/불러오기 ─────────────
    def _load_saved_credentials(self) -> None:
        if not os.path.exists(CONFIG_PATH):
            return
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.api_key_var.set(data.get("api_key", ""))
            self.api_secret_var.set(data.get("api_secret", ""))
            self.tg_token_var.set(data.get("tg_token", ""))
            self.tg_chat_var.set(data.get("tg_chat", ""))
        except Exception:
            pass

    def _save_credentials(self) -> None:
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "api_key": self.api_key_var.get().strip(),
                        "api_secret": self.api_secret_var.get().strip(),
                        "tg_token": self.tg_token_var.get().strip(),
                        "tg_chat": self.tg_chat_var.get().strip(),
                    },
                    f,
                )
        except Exception as e:
            self._log(f"자격증명 저장 실패: {e}")

    # ───────────── 시작/정지 ─────────────
    def _on_start_clicked(self) -> None:
        api_key = self.api_key_var.get().strip()
        api_secret = self.api_secret_var.get().strip()
        if not api_key or not api_secret:
            messagebox.showerror("입력 오류", "Gate.io API Key와 Secret을 입력하세요.")
            return

        confirmed = messagebox.askyesno(
            "실거래 시작 확인",
            "실제 자금으로 Gate.io에서 자동매매를 시작합니다.\n\n"
            f"- 레버리지 {core.LEVERAGE}배, 마틴게일(최대 8배 물타기) 전략입니다.\n"
            "- 시장 상황에 따라 원금 손실이 발생할 수 있습니다.\n\n"
            "계속하시겠습니까?",
        )
        if not confirmed:
            return

        self._save_credentials()
        core.TELEGRAM_BOT_TOKEN = self.tg_token_var.get().strip()
        core.TELEGRAM_CHAT_ID = self.tg_chat_var.get().strip()

        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.status_var.set("상태: 계좌 연결 중...")
        self.stop_event.clear()

        self.worker_thread = threading.Thread(target=self._run_bot, args=(api_key, api_secret), daemon=True)
        self.worker_thread.start()

    def _run_bot(self, api_key: str, api_secret: str) -> None:
        try:
            broker = core.LiveBroker(core.EXCHANGE_ID, api_key, api_secret)
            notifier = core.TelegramNotifier(core.TELEGRAM_BOT_TOKEN, core.TELEGRAM_CHAT_ID)
            bot = core.HedgedMartingaleBot(broker, notifier, mode_label="LIVE")
            market_data = core.PublicMarketData()
        except Exception as e:
            self._log(f"[오류] 계좌 연결 실패: {e}")
            self.root.after(0, self._set_stopped_ui)
            return

        self.root.after(0, lambda: self.status_var.set("상태: 매매 중 (실거래)"))
        self._log("자동매매를 시작합니다 (실거래 모드).")

        bot.run_forever(market_data, poll_sec=core.POLL_SEC, stop_event=self.stop_event)

        self._log("자동매매가 정지되었습니다.")
        self.root.after(0, self._set_stopped_ui)

    def _on_stop_clicked(self) -> None:
        self.status_var.set("상태: 정지 중...")
        self.stop_event.set()

    def _set_stopped_ui(self) -> None:
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.status_var.set("상태: 대기 중")

    def _on_close(self) -> None:
        self.stop_event.set()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    HedgedMartingaleGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
