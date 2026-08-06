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
from tkinter import messagebox

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
        tk.Label(api_frame, text=f"Gate.io 선물 ({core.SYMBOL}) · 양방향 Hedge Mode",
                 font=("맑은 고딕", 9, "bold")).grid(row=0, column=1, columnspan=2, sticky="w", pady=5)

        tk.Label(api_frame, text="API Key:").grid(row=1, column=0, sticky="w")
        self.api_key_var = tk.StringVar()
        tk.Entry(api_frame, textvariable=self.api_key_var, width=55, show="*").grid(
            row=1, column=1, columnspan=2, sticky="w", pady=5)

        tk.Label(api_frame, text="Secret Key:").grid(row=2, column=0, sticky="w")
        self.api_secret_var = tk.StringVar()
        tk.Entry(api_frame, textvariable=self.api_secret_var, width=55, show="*").grid(
            row=2, column=1, columnspan=2, sticky="w", pady=5)

        tk.Label(api_frame, text="텔레그램 봇 토큰:").grid(row=3, column=0, sticky="w")
        self.tg_token_var = tk.StringVar()
        tk.Entry(api_frame, textvariable=self.tg_token_var, width=55, show="*").grid(
            row=3, column=1, columnspan=2, sticky="w", pady=5)

        tk.Label(api_frame, text="텔레그램 Chat ID:").grid(row=4, column=0, sticky="w")
        self.tg_chat_var = tk.StringVar()
        tk.Entry(api_frame, textvariable=self.tg_chat_var, width=55).grid(
            row=4, column=1, columnspan=2, sticky="w", pady=5)
        tk.Label(api_frame, text="※ 텔레그램 2칸은 선택사항입니다. 입력하면 진입/익절/손절 때마다 알림이 옵니다.",
                 fg="#7f8c8d", font=("맑은 고딕", 8)).grid(row=5, column=0, columnspan=3, sticky="w")

        del_row = tk.Frame(api_frame)
        del_row.grid(row=6, column=0, columnspan=3, sticky="w", pady=(5, 0))
        tk.Label(del_row, text="입력한 값은 자동 저장되어 다음 실행 시 자동 입력됩니다.").pack(side="left")
        tk.Button(del_row, text="저장된 키 삭제", command=self._clear_saved_credentials).pack(side="left", padx=(10, 0))
        tk.Label(api_frame, text="⚠️ 이 PC에 평문으로 저장됩니다. 본인 개인 PC에서만 사용하세요.",
                 fg="#c0392b", font=("맑은 고딕", 8)).grid(row=7, column=0, columnspan=3, sticky="w", pady=(2, 0))

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

        tk.Label(config_frame, text="익절 (%):").grid(row=2, column=0, sticky="w")
        self.tp_pct_var = tk.StringVar(value=f"{core.TP_PCT * 100:g}")
        tk.Entry(config_frame, textvariable=self.tp_pct_var, width=18).grid(row=2, column=1, sticky="w", pady=5)
        tk.Label(config_frame, text="(평단가 대비 이만큼 유리하게 움직이면 전량 익절)",
                 fg="#7f8c8d", font=("맑은 고딕", 8)).grid(row=2, column=2, sticky="w", padx=(8, 0))

        tk.Label(config_frame, text="물타기 간격 (%):").grid(row=3, column=0, sticky="w")
        self.step_pct_var = tk.StringVar(value=f"{core.STEP_TRIGGER_PCT * 100:g}")
        tk.Entry(config_frame, textvariable=self.step_pct_var, width=18).grid(row=3, column=1, sticky="w", pady=5)
        tk.Label(config_frame, text=f"(이만큼 불리해질 때마다 1→2→4→8배 추가 진입, 최대 {core.MAX_STEPS}단계)",
                 fg="#7f8c8d", font=("맑은 고딕", 8)).grid(row=3, column=2, sticky="w", padx=(8, 0))

        tk.Label(config_frame, text="손절 (%):").grid(row=4, column=0, sticky="w")
        self.sl_pct_var = tk.StringVar(value=f"{core.STOP_LOSS_PCT * 100:g}")
        tk.Entry(config_frame, textvariable=self.sl_pct_var, width=18).grid(row=4, column=1, sticky="w", pady=5)
        tk.Label(config_frame, text="(평단가 대비 이만큼 불리해지면 단계와 무관하게 전량 손절)",
                 fg="#7f8c8d", font=("맑은 고딕", 8)).grid(row=4, column=2, sticky="w", padx=(8, 0))

        tk.Label(config_frame,
                 text="※ 손절 %는 물타기 간격 %보다 크게 설정해야 합니다. 롱/숏은 서로 독립적으로 동시에 운용됩니다.",
                 fg="#7f8c8d", font=("맑은 고딕", 8), justify="left", anchor="w").grid(
            row=5, column=0, columnspan=3, sticky="w")

        # 모든 입력값은 타이핑할 때마다 자동 저장되어, 프로그램을 강제 종료해도 다음 실행 시 그대로 남는다.
        for var in (self.api_key_var, self.api_secret_var, self.tg_token_var, self.tg_chat_var,
                    self.leverage_var, self.pos_pct_var, self.tp_pct_var, self.step_pct_var, self.sl_pct_var):
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
            if data.get("leverage"):
                self.leverage_var.set(str(data["leverage"]))
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
                        "leverage": self.leverage_var.get().strip(),
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

    def _clear_saved_credentials(self) -> None:
        """저장된 API 키/텔레그램 정보를 삭제한다(전략 설정값은 그대로 유지)."""
        if not messagebox.askyesno(
            "저장된 키 삭제",
            "저장된 API Key / Secret Key / 텔레그램 정보를 삭제합니다.\n"
            "레버리지 등 전략 설정값은 그대로 유지됩니다.\n\n계속할까요?",
        ):
            return
        self.api_key_var.set("")
        self.api_secret_var.set("")
        self.tg_token_var.set("")
        self.tg_chat_var.set("")
        self._save_credentials()
        self._log("저장된 API 키와 텔레그램 정보를 삭제했습니다.")
        messagebox.showinfo("완료", "저장된 API 키를 삭제했습니다.")

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
        core.TP_PCT = tp_pct
        core.STEP_TRIGGER_PCT = step_pct
        core.STOP_LOSS_PCT = sl_pct

        self._save_credentials()
        core.TELEGRAM_BOT_TOKEN = self.tg_token_var.get().strip()
        core.TELEGRAM_CHAT_ID = self.tg_chat_var.get().strip()
        self._log(
            f"전략 설정 적용: 레버리지 {leverage}x / 포지션 크기 {pos_pct*100:g}% / 익절 {tp_pct*100:g}% / "
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
