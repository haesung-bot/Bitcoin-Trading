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
        self._save_after_id: str | None = None

        self._build_widgets()
        self._load_saved_credentials()
        self._install_log_handler()
        self._poll_log_queue()
        self._log(f"프로그램 버전: {core.VERSION}")
        self._log("프로그램이 시작되었습니다. API Key/Secret 입력 후 '매매 시작'을 누르세요.")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ───────────── UI 구성 ─────────────
    def _build_widgets(self) -> None:
        info = (
            f"거래소: Gate.io 선물({core.SYMBOL}) / 레버리지: {core.LEVERAGE}x (양방향 Hedge Mode)\n"
            "포지션 크기 %: 잔고 대비 진입 마진 비율(예: 2 → 레버리지 포함 명목가치는 잔고의 20%)\n"
            "익절 %: 평단가 대비 이만큼 유리하게 움직이면 전량 익절\n"
            "물타기 간격 %: 평단가 대비 이만큼 불리하게 갈 때마다 1→2→4→8배 추가 진입(최대 4단계)\n"
            "손절 %: 평단가 대비 이만큼 불리해지면 단계와 무관하게 전량 손절 (물타기 간격보다 크게 설정)"
        )
        tk.Label(self.root, text=info, justify="left", fg="#333").pack(anchor="w", padx=12, pady=(10, 6))

        form = tk.Frame(self.root)
        form.pack(fill="x", padx=12)

        tk.Label(form, text="Gate.io API Key").grid(row=0, column=0, sticky="w", pady=3)
        self.api_key_var = tk.StringVar()
        tk.Entry(form, textvariable=self.api_key_var, width=50).grid(row=0, column=1, columnspan=3, pady=3, sticky="we")

        tk.Label(form, text="Gate.io API Secret").grid(row=1, column=0, sticky="w", pady=3)
        self.api_secret_var = tk.StringVar()
        tk.Entry(form, textvariable=self.api_secret_var, width=50, show="*").grid(row=1, column=1, columnspan=3, pady=3, sticky="we")

        tk.Label(form, text="텔레그램 봇 토큰(선택)").grid(row=2, column=0, sticky="w", pady=3)
        self.tg_token_var = tk.StringVar()
        tk.Entry(form, textvariable=self.tg_token_var, width=50).grid(row=2, column=1, columnspan=3, pady=3, sticky="we")

        tk.Label(form, text="텔레그램 Chat ID(선택)").grid(row=3, column=0, sticky="w", pady=3)
        self.tg_chat_var = tk.StringVar()
        tk.Entry(form, textvariable=self.tg_chat_var, width=50).grid(row=3, column=1, columnspan=3, pady=3, sticky="we")

        # ── 전략 설정(%) ──
        tk.Label(form, text="포지션 크기 %").grid(row=4, column=0, sticky="w", pady=3)
        self.pos_pct_var = tk.StringVar(value=f"{core.INITIAL_MARGIN_PCT * 100:g}")
        tk.Entry(form, textvariable=self.pos_pct_var, width=10).grid(row=4, column=1, pady=3, sticky="w")

        tk.Label(form, text="익절 %").grid(row=4, column=2, sticky="e", padx=(10, 4), pady=3)
        self.tp_pct_var = tk.StringVar(value=f"{core.TP_PCT * 100:g}")
        tk.Entry(form, textvariable=self.tp_pct_var, width=10).grid(row=4, column=3, pady=3, sticky="w")

        tk.Label(form, text="물타기 간격 %").grid(row=5, column=0, sticky="w", pady=3)
        self.step_pct_var = tk.StringVar(value=f"{core.STEP_TRIGGER_PCT * 100:g}")
        tk.Entry(form, textvariable=self.step_pct_var, width=10).grid(row=5, column=1, pady=3, sticky="w")

        tk.Label(form, text="손절 %").grid(row=5, column=2, sticky="e", padx=(10, 4), pady=3)
        self.sl_pct_var = tk.StringVar(value=f"{core.STOP_LOSS_PCT * 100:g}")
        tk.Entry(form, textvariable=self.sl_pct_var, width=10).grid(row=5, column=3, pady=3, sticky="w")

        form.columnconfigure(1, weight=1)
        form.columnconfigure(3, weight=1)

        # 모든 입력값은 타이핑할 때마다 자동 저장되어, 프로그램을 강제 종료해도 다음 실행 시 그대로 남는다.
        for var in (self.api_key_var, self.api_secret_var, self.tg_token_var, self.tg_chat_var,
                    self.pos_pct_var, self.tp_pct_var, self.step_pct_var, self.sl_pct_var):
            var.trace_add("write", self._schedule_save)

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

        self.reset_btn = tk.Button(
            self.root, text="⟳ 저장된 매매상태 초기화 (정지 후 사용)", bg="#e0e0e0",
            command=self._on_reset_clicked,
        )
        self.reset_btn.pack(fill="x", padx=12, pady=(0, 4))

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
            if data.get("pos_pct"):
                self.pos_pct_var.set(str(data["pos_pct"]))
            if data.get("tp_pct"):
                self.tp_pct_var.set(str(data["tp_pct"]))
            if data.get("step_pct"):
                self.step_pct_var.set(str(data["step_pct"]))
            if data.get("sl_pct"):
                self.sl_pct_var.set(str(data["sl_pct"]))
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
                        "pos_pct": self.pos_pct_var.get().strip(),
                        "tp_pct": self.tp_pct_var.get().strip(),
                        "step_pct": self.step_pct_var.get().strip(),
                        "sl_pct": self.sl_pct_var.get().strip(),
                    },
                    f,
                )
        except Exception as e:
            self._log(f"설정 저장 실패: {e}")

    def _schedule_save(self, *_args) -> None:
        """입력칸이 바뀔 때마다 호출되며, 타이핑이 잠시 멈춘 뒤(500ms) 한 번만 저장한다."""
        if self._save_after_id is not None:
            self.root.after_cancel(self._save_after_id)
        self._save_after_id = self.root.after(500, self._save_credentials)

    def _parse_pct(self, text: str, name: str) -> float:
        """'2', '0.3' 같은 퍼센트 입력을 소수(0.02, 0.003)로 변환. 잘못된 값이면 ValueError."""
        value = float(text.strip())
        if value <= 0:
            raise ValueError(f"{name}는 0보다 커야 합니다.")
        return value / 100.0

    # ───────────── 시작/정지 ─────────────
    def _on_start_clicked(self) -> None:
        api_key = self.api_key_var.get().strip()
        api_secret = self.api_secret_var.get().strip()
        if not api_key or not api_secret:
            messagebox.showerror("입력 오류", "Gate.io API Key와 Secret을 입력하세요.")
            return

        # 전략 % 값 검증 및 적용
        try:
            pos_pct = self._parse_pct(self.pos_pct_var.get(), "포지션 크기 %")
            tp_pct = self._parse_pct(self.tp_pct_var.get(), "익절 %")
            step_pct = self._parse_pct(self.step_pct_var.get(), "물타기 간격 %")
            sl_pct = self._parse_pct(self.sl_pct_var.get(), "손절 %")
        except ValueError as e:
            messagebox.showerror("입력 오류", f"포지션/익절/물타기 간격/손절 %는 0보다 큰 숫자여야 합니다.\n({e})")
            return

        if sl_pct <= step_pct:
            messagebox.showerror(
                "입력 오류",
                f"손절 %({sl_pct*100:g}%)는 물타기 간격 %({step_pct*100:g}%)보다 커야 합니다.\n"
                "손절이 물타기 간격보다 작거나 같으면 물타기 없이 바로 손절됩니다.",
            )
            return

        core.INITIAL_MARGIN_PCT = pos_pct
        core.TP_PCT = tp_pct
        core.STEP_TRIGGER_PCT = step_pct
        core.STOP_LOSS_PCT = sl_pct

        self._save_credentials()
        core.TELEGRAM_BOT_TOKEN = self.tg_token_var.get().strip()
        core.TELEGRAM_CHAT_ID = self.tg_chat_var.get().strip()
        self._log(
            f"전략 설정 적용: 포지션 크기 {pos_pct*100:g}% / 익절 {tp_pct*100:g}% / "
            f"물타기 간격 {step_pct*100:g}% / 손절 {sl_pct*100:g}%"
        )

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
            bot = core.HedgedMartingaleBot(broker, notifier, mode_label="LIVE", state_path=core.STATE_PATH)
            market_data = core.PublicMarketData()
        except Exception as e:
            self._log(f"[오류] 계좌 연결 실패: {e}")
            self.root.after(0, self._set_stopped_ui)
            return

        self.root.after(0, lambda: self.status_var.set("상태: 매매 중 (실거래)"))
        self._log("자동매매를 시작합니다 (실거래 모드).")

        # 텔레그램이 실제로 연결되는지 시작 시점에 바로 확인(첫 매매까지 기다리지 않도록)
        if core.TELEGRAM_BOT_TOKEN and core.TELEGRAM_CHAT_ID:
            self._log("텔레그램 연결 테스트 메시지를 보냅니다...")
            notifier.send("[LIVE] 자동매매가 시작되었습니다. 이 메시지가 보이면 텔레그램 알림이 정상 연결된 것입니다.")
        else:
            self._log("텔레그램 토큰/Chat ID가 비어 있어 알림 없이 진행합니다(로그에는 계속 표시됨).")

        bot.run_forever(market_data, poll_sec=core.POLL_SEC, stop_event=self.stop_event)

        self._log("자동매매가 정지되었습니다.")
        self.root.after(0, self._set_stopped_ui)

    def _on_stop_clicked(self) -> None:
        self.status_var.set("상태: 정지 중...")
        self.stop_event.set()

    def _on_reset_clicked(self) -> None:
        if self.worker_thread is not None and self.worker_thread.is_alive():
            messagebox.showwarning("실행 중", "먼저 '■ 정지'를 누른 뒤 상태를 초기화하세요.")
            return
        confirmed = messagebox.askyesno(
            "저장된 매매상태 초기화",
            "저장된 매매 진행 상태를 삭제하고 다음 시작 시 처음부터 진행합니다.\n\n"
            "⚠️ 거래소(Gate.io)에 실제로 열려있는 포지션은 이 버튼으로 삭제되지 않습니다.\n"
            "포지션이 남아 있으면 Gate.io에서 직접 청산한 뒤 초기화하세요.\n\n"
            "계속할까요?",
        )
        if not confirmed:
            return
        try:
            if os.path.exists(core.STATE_PATH):
                os.remove(core.STATE_PATH)
                self._log("저장된 매매 상태를 초기화했습니다. 다음 시작은 포지션 0에서 새로 진행됩니다.")
            else:
                self._log("저장된 매매 상태 파일이 없습니다(이미 초기 상태).")
        except Exception as e:
            self._log(f"상태 초기화 실패: {e}")

    def _set_stopped_ui(self) -> None:
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.status_var.set("상태: 대기 중")

    def _on_close(self) -> None:
        self._save_credentials()
        self.stop_event.set()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    HedgedMartingaleGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
