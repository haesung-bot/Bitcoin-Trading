"""
hedged_martingale_bot_gui.py
비트코인 양방향(Hedge Mode) 마틴게일 자동매매 - Gate.io 실거래 GUI

hedged_martingale_bot.py의 전략 엔진을 그대로 사용한다. API 키를 입력하고
'자동매매 시작' 버튼을 누르면 실제 Gate.io 계좌로 주문이 나가는 실거래가 시작된다
(모의매매 아님). 레버리지/포지션 크기/익절/물타기 간격/손절은 화면에서 직접 설정한다.

화면 구성은 gateio_supertrend_bot.py와 동일한 스타일(스크롤 컨테이너 + LabelFrame
섹션 + 어두운 로그창)을 따른다.

exe로 빌드하는 방법은 hedged_martingale_bot_exe_빌드_방법.md 참고.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

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
        self.root.geometry("720x880")
        self.root.minsize(680, 580)
        self.root.resizable(True, True)

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.stop_event = threading.Event()
        self.worker_thread: threading.Thread | None = None
        self._save_after_id: str | None = None

        self._create_scrollable_container()
        self._build_widgets()
        self._load_saved_credentials()
        self._install_log_handler()
        self._poll_log_queue()
        self._log(f"ℹ️ 프로그램 버전: {core.VERSION}")
        self._log("ℹ️ 프로그램이 시작되었습니다. API Key 입력 후 '자동매매 시작' 버튼을 눌러주세요.")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _create_scrollable_container(self) -> None:
        """창을 줄여도 스크롤(휠/스크롤바)로 전체 내용을 볼 수 있도록 캔버스로 감싼다."""
        container = tk.Frame(self.root)
        container.pack(fill="both", expand=True)

        self.main_canvas = tk.Canvas(container, highlightthickness=0)
        main_scrollbar = tk.Scrollbar(container, orient="vertical", command=self.main_canvas.yview)
        self.scrollable_frame = tk.Frame(self.main_canvas)

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.main_canvas.configure(scrollregion=self.main_canvas.bbox("all")),
        )
        self.canvas_window = self.main_canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.main_canvas.configure(yscrollcommand=main_scrollbar.set)

        # 캔버스 폭이 바뀌면 내부 프레임 폭도 같이 맞춰서, 가로로는 안 잘리고 내용이 꽉 차게 한다
        self.main_canvas.bind(
            "<Configure>",
            lambda e: self.main_canvas.itemconfig(self.canvas_window, width=e.width),
        )
        self.main_canvas.pack(side="left", fill="both", expand=True)
        main_scrollbar.pack(side="right", fill="y")

        def _on_mousewheel(event):
            self.main_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        self.main_canvas.bind_all("<MouseWheel>", _on_mousewheel)

    # ───────────── UI 구성 ─────────────
    def _build_widgets(self) -> None:
        parent = self.scrollable_frame

        # 1. 거래소 / API 설정
        api_frame = tk.LabelFrame(parent, text=" 거래소 / API 설정 ", padx=10, pady=10)
        api_frame.pack(fill="x", padx=15, pady=5)

        tk.Label(api_frame, text="거래소:").grid(row=0, column=0, sticky="w")
        self.exchange_var = tk.StringVar(value=core.EXCHANGE_NAME)
        self.combo_exchange = ttk.Combobox(
            api_frame, textvariable=self.exchange_var,
            values=list(core.EXCHANGE_OPTIONS.keys()), state="readonly", width=15,
        )
        self.combo_exchange.grid(row=0, column=1, sticky="w", pady=5)
        self.combo_exchange.bind("<<ComboboxSelected>>", self._on_exchange_changed)

        self.btn_api_guide = tk.Button(api_frame, text="📖 Gate.io API Key 발급방법", bg="#f39c12", fg="white",
                                       command=self._show_api_guide)
        self.btn_api_guide.grid(row=0, column=2, sticky="w", padx=(10, 0), pady=5)

        tk.Label(api_frame, text="API Key:").grid(row=1, column=0, sticky="w")
        self.api_key_var = tk.StringVar()
        tk.Entry(api_frame, textvariable=self.api_key_var, width=55, show="*").grid(
            row=1, column=1, columnspan=2, sticky="w", pady=5)

        tk.Label(api_frame, text="Secret Key:").grid(row=2, column=0, sticky="w")
        self.api_secret_var = tk.StringVar()
        tk.Entry(api_frame, textvariable=self.api_secret_var, width=55, show="*").grid(
            row=2, column=1, columnspan=2, sticky="w", pady=5)

        self.label_passphrase = tk.Label(api_frame, text="Passphrase:")
        self.passphrase_var = tk.StringVar()
        self.entry_passphrase = tk.Entry(api_frame, textvariable=self.passphrase_var, width=55, show="*")
        self.label_passphrase.grid(row=3, column=0, sticky="w")
        self.entry_passphrase.grid(row=3, column=1, columnspan=2, sticky="w", pady=5)
        self.label_passphrase_note = tk.Label(
            api_frame, text="※ 이 거래소는 API Key/Secret Key 외에 Passphrase도 필요합니다.",
            fg="#e67e22", font=("맑은 고딕", 8))
        self.label_passphrase_note.grid(row=4, column=0, columnspan=3, sticky="w")

        self.save_keys_var = tk.BooleanVar(value=True)
        save_row = tk.Frame(api_frame)
        save_row.grid(row=5, column=0, columnspan=3, sticky="w", pady=(5, 0))
        tk.Checkbutton(save_row, text="API 키 저장 (다음에 자동 입력)", variable=self.save_keys_var,
                       command=self._schedule_save).pack(side="left")
        tk.Button(save_row, text="저장된 키 삭제", command=self._clear_saved_credentials).pack(side="left", padx=(10, 0))
        tk.Label(api_frame, text="⚠️ 체크 시 이 PC에 평문으로 저장됩니다. 본인 개인 PC에서만 사용하세요.",
                 fg="#c0392b", font=("맑은 고딕", 8)).grid(row=6, column=0, columnspan=3, sticky="w", pady=(2, 0))
        tk.Label(api_frame, text="※ 거래소마다 API 키가 다릅니다. 거래소를 바꾸면 그 거래소용으로 저장된 키가 자동으로 불러와집니다.",
                 fg="#7f8c8d", font=("맑은 고딕", 8)).grid(row=7, column=0, columnspan=3, sticky="w")

        self._update_passphrase_visibility()

        # 2. 상세설정
        config_frame = tk.LabelFrame(parent, text=" 상세설정 ", padx=10, pady=10)
        config_frame.pack(fill="x", padx=15, pady=5)

        tk.Label(config_frame, text="레버리지 (1~100배):").grid(row=0, column=0, sticky="w")
        self.leverage_var = tk.StringVar(value=f"{core.LEVERAGE:g}")
        tk.Spinbox(config_frame, from_=1, to=100, increment=1, width=15,
                   textvariable=self.leverage_var).grid(row=0, column=1, sticky="w", pady=5)
        tk.Label(config_frame, text="(매매 시작 시 거래소 계좌에 설정됩니다)",
                 fg="#7f8c8d", font=("맑은 고딕", 8)).grid(row=0, column=2, sticky="w", padx=(8, 0))

        tk.Label(config_frame, text="포지션 크기 (%):").grid(row=1, column=0, sticky="w")
        self.pos_pct_var = tk.StringVar(value=f"{core.INITIAL_MARGIN_PCT * 100:g}")
        tk.Entry(config_frame, textvariable=self.pos_pct_var, width=18).grid(row=1, column=1, sticky="w", pady=5)
        tk.Label(config_frame, text="(잔고 대비 1차 진입 마진. 예: 잔고 100 × 레버리지 10배 × 1% ≈ 10 USDT 진입)",
                 fg="#7f8c8d", font=("맑은 고딕", 8)).grid(row=1, column=2, sticky="w", padx=(8, 0))

        tk.Label(
            config_frame,
            text=(f"※ 익절 {core.TP_PCT*100:g}% / 물타기 간격 {core.STEP_TRIGGER_PCT*100:g}% "
                  f"(최대 {core.MAX_STEPS}단계, 1→2→4→8배) / 손절 {core.STOP_LOSS_PCT*100:g}% 로 고정 운용됩니다.\n"
                  "   롱/숏은 서로 독립적으로 동시에 운용됩니다."),
            fg="#7f8c8d", font=("맑은 고딕", 8), justify="left", anchor="w").grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(5, 0))

        # 모든 입력값은 타이핑할 때마다 자동 저장되어, 프로그램을 강제 종료해도 다음 실행 시 그대로 남는다.
        for var in (self.api_key_var, self.api_secret_var, self.passphrase_var,
                    self.leverage_var, self.pos_pct_var):
            var.trace_add("write", self._schedule_save)

        # 3. 제어 버튼
        btn_frame = tk.Frame(parent, pady=10)
        btn_frame.pack(fill="x", padx=15)

        self.start_btn = tk.Button(btn_frame, text="▶ 자동매매 시작", bg="#2ecc71", fg="white",
                                   font=("맑은 고딕", 12, "bold"), height=1, command=self._on_start_clicked)
        self.start_btn.pack(side="left", fill="x", expand=True, padx=5)

        self.stop_btn = tk.Button(btn_frame, text="■ 정지", bg="#e74c3c", fg="white",
                                  font=("맑은 고딕", 12, "bold"), height=1, state="disabled",
                                  command=self._on_stop_clicked)
        self.stop_btn.pack(side="right", fill="x", expand=True, padx=5)

        reset_btn_frame = tk.Frame(parent)
        reset_btn_frame.pack(fill="x", padx=15, pady=(0, 5))
        self.reset_btn = tk.Button(reset_btn_frame, text="⟳ 저장된 매매상태 초기화 (정지 후 사용)",
                                   bg="#34495e", fg="white", font=("맑은 고딕", 10, "bold"),
                                   command=self._on_reset_clicked)
        self.reset_btn.pack(fill="x")

        # 4. 실시간 로그 창
        log_frame = tk.LabelFrame(parent, text=" 실시간 매매 로그 및 상태 ", padx=10, pady=10)
        log_frame.pack(fill="both", expand=True, padx=15, pady=5)

        self.status_var = tk.StringVar(value="상태: 대기 중")
        tk.Label(log_frame, textvariable=self.status_var, anchor="w",
                 font=("맑은 고딕", 10, "bold")).pack(fill="x", pady=(0, 5))

        log_inner = tk.Frame(log_frame)
        log_inner.pack(fill="both", expand=True)

        log_scrollbar = tk.Scrollbar(log_inner)
        log_scrollbar.pack(side="right", fill="y")

        self.log_box = tk.Text(log_inner, height=15, width=70, state="disabled", bg="#1e1e1e", fg="#ffffff",
                               yscrollcommand=log_scrollbar.set, wrap="word")
        self.log_box.pack(side="left", fill="both", expand=True)
        log_scrollbar.config(command=self.log_box.yview)

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
        # 로그가 너무 쌓이면 UI가 느려지므로 500줄 초과 시 오래된 줄부터 제거
        line_count = int(self.log_box.index("end-1c").split(".")[0])
        if line_count > 500:
            self.log_box.delete("1.0", f"{line_count - 500}.0")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _log(self, text: str) -> None:
        self.log_queue.put(text)

    # ───────────── 거래소 선택 / API 발급 안내 ─────────────
    def _on_exchange_changed(self, _event=None) -> None:
        """거래소를 바꾸면 그 거래소용으로 저장된 키를 불러오고 Passphrase 칸을 갱신한다."""
        name = self.exchange_var.get()
        self.btn_api_guide.config(text=f"📖 {name} API Key 발급방법")
        self._update_passphrase_visibility()
        self._load_saved_credentials(exchange_name=name)
        self._log(f"거래소를 {name}(으)로 변경했습니다.")

    def _update_passphrase_visibility(self) -> None:
        """OKX/Bitget처럼 Passphrase가 필요한 거래소에서만 해당 입력칸을 보여준다."""
        name = self.exchange_var.get()
        needs = core.EXCHANGE_OPTIONS.get(name, {}).get("needs_passphrase", False)
        if needs:
            self.label_passphrase.grid()
            self.entry_passphrase.grid()
            self.label_passphrase_note.grid()
        else:
            self.label_passphrase.grid_remove()
            self.entry_passphrase.grid_remove()
            self.label_passphrase_note.grid_remove()

    def _show_api_guide(self) -> None:
        name = self.exchange_var.get()
        guide = core.EXCHANGE_API_GUIDES.get(name, "안내 준비 중입니다.")

        win = tk.Toplevel(self.root)
        win.title(f"{name} API Key 발급 방법")
        win.geometry("640x620")
        tk.Label(win, text=f"{name} API Key 발급 방법", font=("맑은 고딕", 13, "bold")).pack(pady=(12, 6))

        text_frame = tk.Frame(win)
        text_frame.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        sb = tk.Scrollbar(text_frame)
        sb.pack(side="right", fill="y")
        txt = tk.Text(text_frame, wrap="word", yscrollcommand=sb.set, font=("맑은 고딕", 10))
        txt.pack(side="left", fill="both", expand=True)
        sb.config(command=txt.yview)
        txt.insert("1.0", guide)
        txt.config(state="disabled")

        tk.Button(win, text="닫기", command=win.destroy, width=12).pack(pady=(0, 12))

    # ───────────── 자격증명 저장/불러오기 (거래소별) ─────────────
    def _read_config(self) -> dict:
        if not os.path.exists(CONFIG_PATH):
            return {}
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _load_saved_credentials(self, exchange_name: str = None) -> None:
        data = self._read_config()
        name = exchange_name or data.get("last_exchange") or self.exchange_var.get()
        if name in core.EXCHANGE_OPTIONS and not exchange_name:
            self.exchange_var.set(name)
            self.btn_api_guide.config(text=f"📖 {name} API Key 발급방법")
            self._update_passphrase_visibility()

        creds = (data.get("exchanges") or {}).get(name, {})
        self.api_key_var.set(creds.get("api_key", ""))
        self.api_secret_var.set(creds.get("api_secret", ""))
        self.passphrase_var.set(creds.get("passphrase", ""))

        if data.get("save_keys") is not None:
            self.save_keys_var.set(bool(data["save_keys"]))
        if data.get("leverage"):
            self.leverage_var.set(str(data["leverage"]))
        if data.get("pos_pct"):
            self.pos_pct_var.set(str(data["pos_pct"]))

    def _save_credentials(self) -> None:
        try:
            data = self._read_config()
            name = self.exchange_var.get()
            exchanges = data.get("exchanges") or {}
            if self.save_keys_var.get():
                exchanges[name] = {
                    "api_key": self.api_key_var.get().strip(),
                    "api_secret": self.api_secret_var.get().strip(),
                    "passphrase": self.passphrase_var.get().strip(),
                }
            else:
                exchanges.pop(name, None)  # 저장 해제 시 그 거래소 키는 남기지 않는다
            data.update({
                "last_exchange": name,
                "exchanges": exchanges,
                "save_keys": bool(self.save_keys_var.get()),
                "leverage": self.leverage_var.get().strip(),
                "pos_pct": self.pos_pct_var.get().strip(),
            })
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception as e:
            self._log(f"설정 저장 실패: {e}")

    def _schedule_save(self, *_args) -> None:
        """입력칸이 바뀔 때마다 호출되며, 타이핑이 잠시 멈춘 뒤(500ms) 한 번만 저장한다."""
        if self._save_after_id is not None:
            self.root.after_cancel(self._save_after_id)
        self._save_after_id = self.root.after(500, self._save_credentials)

    def _clear_saved_credentials(self) -> None:
        """현재 선택된 거래소용으로 저장된 API 키를 삭제한다."""
        name = self.exchange_var.get()
        if not messagebox.askyesno(
            "저장된 키 삭제",
            f"{name}용으로 저장된 API Key / Secret Key / Passphrase를 삭제합니다.\n"
            "다른 거래소의 키와 레버리지 등 설정값은 그대로 유지됩니다.\n\n계속할까요?",
        ):
            return
        self.api_key_var.set("")
        self.api_secret_var.set("")
        self.passphrase_var.set("")
        try:
            data = self._read_config()
            exchanges = data.get("exchanges") or {}
            exchanges.pop(name, None)
            data["exchanges"] = exchanges
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception as e:
            self._log(f"키 삭제 실패: {e}")
            return
        self._log(f"{name}용으로 저장된 API 키를 삭제했습니다.")
        messagebox.showinfo("완료", f"{name}용으로 저장된 API 키를 삭제했습니다.")

    def _parse_pct(self, text: str, name: str) -> float:
        """'2', '0.3' 같은 퍼센트 입력을 소수(0.02, 0.003)로 변환. 잘못된 값이면 ValueError."""
        value = float(text.strip())
        if value <= 0:
            raise ValueError(f"{name}는 0보다 커야 합니다.")
        return value / 100.0

    # ───────────── 시작/정지 ─────────────
    def _on_start_clicked(self) -> None:
        exchange_name = self.exchange_var.get()
        api_key = self.api_key_var.get().strip()
        api_secret = self.api_secret_var.get().strip()
        passphrase = self.passphrase_var.get().strip()

        if not api_key or not api_secret:
            messagebox.showerror("오류", f"{exchange_name} API Key와 Secret Key를 입력해주세요.")
            return

        needs_passphrase = core.EXCHANGE_OPTIONS[exchange_name]["needs_passphrase"]
        if needs_passphrase and not passphrase:
            messagebox.showerror(
                "오류",
                f"{exchange_name}는 API Key/Secret Key 외에 Passphrase도 필요합니다.\n입력해주세요.",
            )
            return

        # 전략 값 검증 및 적용
        try:
            leverage = int(float(self.leverage_var.get().strip()))
            if not 1 <= leverage <= 100:
                raise ValueError("레버리지는 1~100 사이여야 합니다.")
        except ValueError as e:
            messagebox.showerror("입력 오류", f"레버리지는 1~100 사이의 숫자여야 합니다.\n({e})")
            return

        try:
            pos_pct = self._parse_pct(self.pos_pct_var.get(), "포지션 크기 %")
        except ValueError as e:
            messagebox.showerror("입력 오류", f"포지션 크기 %는 0보다 큰 숫자여야 합니다.\n({e})")
            return

        # 4단계까지 물타기하면 1차의 15배 명목가치가 필요하므로, 증거금이 잔고를 넘지 않는지 확인
        max_margin_ratio = pos_pct * (2 ** core.MAX_STEPS - 1)
        if max_margin_ratio > 1.0:
            messagebox.showerror(
                "입력 오류",
                f"포지션 크기 {pos_pct*100:g}%로는 {core.MAX_STEPS}단계까지 물타기할 때 "
                f"증거금이 잔고의 {max_margin_ratio*100:.0f}%가 되어 부족해집니다.\n"
                f"포지션 크기를 {100/(2**core.MAX_STEPS-1):.2f}% 이하로 낮추세요.",
            )
            return

        core.LEVERAGE = leverage
        core.INITIAL_MARGIN_PCT = pos_pct

        self._save_credentials()
        self._log(
            f"전략 설정 적용: {exchange_name} / 레버리지 {leverage}x / 포지션 크기 {pos_pct*100:g}% / "
            f"익절 {core.TP_PCT*100:g}% / 물타기 간격 {core.STEP_TRIGGER_PCT*100:g}% / 손절 {core.STOP_LOSS_PCT*100:g}%"
        )

        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.combo_exchange.config(state="disabled")  # 실행 중에는 거래소 변경 금지
        self.status_var.set("상태: 계좌 연결 중...")
        self.stop_event.clear()

        self.worker_thread = threading.Thread(
            target=self._run_bot, args=(exchange_name, api_key, api_secret, passphrase), daemon=True)
        self.worker_thread.start()

    def _run_bot(self, exchange_name: str, api_key: str, api_secret: str, passphrase: str) -> None:
        try:
            broker = core.LiveBroker(exchange_name, api_key, api_secret, passphrase)
            notifier = core.TelegramNotifier(core.TELEGRAM_BOT_TOKEN, core.TELEGRAM_CHAT_ID)
            bot = core.HedgedMartingaleBot(broker, notifier, mode_label="LIVE", state_path=core.STATE_PATH)
            market_data = core.PublicMarketData(exchange_name)
        except Exception as e:
            self._log(f"[오류] 계좌 연결 실패: {e}")
            self.root.after(0, self._set_stopped_ui)
            return

        self.root.after(0, lambda: self.status_var.set(f"상태: 매매 중 (실거래 · {exchange_name})"))
        self._log(f"{exchange_name} 자동매매를 시작합니다 (실거래 모드).")

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
        name = self.exchange_var.get()
        confirmed = messagebox.askyesno(
            "저장된 매매상태 초기화",
            "저장된 매매 진행 상태를 삭제하고 다음 시작 시 처음부터 진행합니다.\n\n"
            f"⚠️ 거래소({name})에 실제로 열려있는 포지션은 이 버튼으로 삭제되지 않습니다.\n"
            f"포지션이 남아 있으면 {name}에서 직접 청산한 뒤 초기화하세요.\n\n"
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
        self.combo_exchange.config(state="readonly")
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
