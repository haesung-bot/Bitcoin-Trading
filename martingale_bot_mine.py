"""
martingale_bot_mine.py
비트코인 선물 자동매매 — 개인용(운영자 전용) GUI

배포용(hedged_martingale_bot_gui.py)과 매매 엔진(hedged_martingale_bot.py)은 완전히 동일하고,
화면만 다르다. 개인용이므로 배포용에서 감춰둔 것들을 전부 열어둔다.

  · 텔레그램 봇 토큰 / Chat ID 입력 + 연결 테스트 버튼
  · 익절 % / 물타기 간격 % / 손절 % 까지 직접 조정
  · '상세 로그'를 켜면 진입 금액 계산식, 주문 실행 내역 등 내부 동작까지 표시
  · 저장된 매매상태 초기화 버튼

설정/기록 파일은 배포용과 겹치지 않도록 별도 경로를 쓴다(두 프로그램을 같이 써도 안전).

실행: python martingale_bot_mine.py
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading

import requests
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

import hedged_martingale_bot as core

CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".martingale_bot_mine_config.json")

# 배포용과 상태/기록이 섞이지 않도록 개인용 전용 경로를 쓴다.
core.STATE_PATH = os.path.join(os.path.expanduser("~"), ".martingale_bot_mine_state.json")
core.TRADE_LOG_PATH = os.path.join(os.path.expanduser("~"), ".martingale_bot_mine_trades.json")

# 개인용은 BTC 수량/계약수 대신 USDT 금액과 진입 차수를 그대로 보여준다.
core.SHOW_QTY_DETAIL = False

# 그룹방으로도 알림을 보내므로 텔레그램 메시지에서는 잔고를 뺀다(화면 로그에는 계속 표시).
core.TELEGRAM_SHOW_BALANCE = False

# 창 높이를 내용 전체 높이의 몇 배로 열지(나머지는 스크롤로 본다).
WINDOW_HEIGHT_RATIO = 0.7


# 이 화면 파일이 엔진 파일에서 반드시 필요로 하는 것들. 하나라도 없으면 버전이 어긋난 것이다.
REQUIRED_CORE_ATTRS = ("HEARTBEAT_MARK", "martingale_ladder", "SHOW_QTY_DETAIL",
                       "TELEGRAM_SHOW_BALANCE", "parse_chat_ids")

def check_core_compatible(root=None) -> bool:
    """엔진 파일(hedged_martingale_bot.py)이 이 화면 파일과 맞는 버전인지 확인한다.

    두 파일 중 하나만 새로 덮어쓰면, 화면은 최신인데 엔진이 구버전이라 없는 값을
    참조하게 된다. 그러면 로그창이 통째로 비어 있고 상태줄이 '계좌 연결 중...'에서
    멈춘 것처럼 보여 원인을 찾기가 어렵다. 그래서 시작할 때 대놓고 알린다.
    """
    missing = [name for name in REQUIRED_CORE_ATTRS if not hasattr(core, name)]
    if not missing:
        return True
    msg = (
        f"엔진 파일이 예전 버전입니다.\n\n"
        f"  · 화면 파일: 최신\n"
        f"  · 엔진 파일: v{getattr(core, 'VERSION', '알 수 없음')}\n\n"
        f"두 파일을 함께 최신으로 바꿔야 합니다.\n"
        f"  1) hedged_martingale_bot.py  (엔진)\n"
        f"  2) {os.path.basename(__file__)}  (화면)\n\n"
        f"exe로 만드셨다면 build, dist 폴더와 .spec 파일을 지우고 다시 빌드하세요.\n"
        f"(예전 빌드가 남아 있으면 새 파일이 반영되지 않습니다.)"
    )
    try:
        messagebox.showerror("파일 버전이 서로 다릅니다", msg)
    except Exception:
        pass
    print(msg)
    return False


class QueueLogHandler(logging.Handler):
    def __init__(self, log_queue: queue.Queue[str]):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record: logging.LogRecord) -> None:
        self.log_queue.put(self.format(record))


class PersonalTradingGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("비트코인 선물 자동매매 — 개인용")

        self.log_queue: queue.Queue[str] = queue.Queue()
        # 매매 스레드에서 화면을 직접 건드리면 Tkinter가 불안정해지므로(스레드 안전하지 않음),
        # 화면 갱신 작업은 이 큐에 넣고 메인 스레드가 꺼내서 실행한다.
        self.ui_queue: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.worker_thread: threading.Thread | None = None
        self._save_after_id: str | None = None
        self._pending_save_exchange: str | None = None
        self._status_after_id: str | None = None
        self._last_line_is_heartbeat = False
        self.bot = None                 # 실행 중인 봇(상태줄에 포지션을 표시하기 위해 보관)
        self.exchange_label = ""
        self._window_fitted = False     # 창 크기를 한 번이라도 맞췄는지(구성 도중 재조정 방지)

        self._build_widgets()
        self._fit_window_to_content()
        self._load_saved_credentials()
        self._load_trade_history()
        self._install_log_handler()
        # 로그 핸들러가 붙은 뒤에 적용해야 한다(_install_log_handler가 레벨을 INFO로 되돌린다).
        self._apply_log_level()
        self._poll_log_queue()
        self._log(f"ℹ️ 개인용 자동매매 v{core.VERSION}")
        self._log(f"ℹ️ 상태파일: {core.STATE_PATH}")
        self._log("ℹ️ API Key 입력 후 '자동매매 시작'을 누르세요. (배포용과 설정/기록이 분리되어 있습니다)")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _fit_window_to_content(self) -> None:
        """창 높이를 내용 전체 높이의 WINDOW_HEIGHT_RATIO만큼으로 잡는다(나머지는 스크롤).

        가로는 내용에 딱 맞추고, 세로만 줄여서 화면을 덜 차지하게 한다. 잘린 부분은
        바깥 스크롤바나 마우스 휠로 내려서 본다. 거래소를 바꿔 Passphrase 칸이
        나타나거나 사라지면 필요한 높이가 달라지므로, 이전에 걸어둔 최소 크기를 먼저
        풀고 다시 측정해야 창이 작아지는 방향으로도 맞춰진다.
        """
        self.root.minsize(1, 1)
        self.root.update_idletasks()
        content_w = self.content.winfo_reqwidth()
        content_h = self.content.winfo_reqheight()
        self.canvas.configure(width=content_w)
        self._sync_scrollregion()
        width = content_w + self.vscroll.winfo_reqwidth() + 2
        height = int(content_h * WINDOW_HEIGHT_RATIO)
        self.root.geometry(f"{width}x{height}")
        # 가로는 고정, 세로는 사용자가 원하면 더 늘릴 수 있게 최소값만 걸어둔다.
        self.root.minsize(width, min(height, 400))
        self._window_fitted = True

    # ───────────── 스크롤 가능한 바깥 틀 ─────────────
    def _build_scroll_shell(self) -> tk.Frame:
        """창 전체를 세로 스크롤할 수 있게 감싸고, 실제 내용이 들어갈 프레임을 돌려준다."""
        shell = tk.Frame(self.root)
        shell.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(shell, highlightthickness=0)
        self.vscroll = tk.Scrollbar(shell, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vscroll.set)
        self.vscroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.content = tk.Frame(self.canvas)
        self._content_id = self.canvas.create_window((0, 0), window=self.content, anchor="nw")

        self.content.bind("<Configure>", lambda e: self._sync_scrollregion())
        # 캔버스가 넓어지면 내용도 같이 넓혀야 fill="x" 섹션들이 제대로 늘어난다.
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfigure(self._content_id, width=e.width))

        # 마우스 휠: 창 어디에서 굴려도 전체가 스크롤되게 한다(로그창 위는 제외).
        self.root.bind_all("<MouseWheel>", self._on_mousewheel)      # Windows / macOS
        self.root.bind_all("<Button-4>", self._on_mousewheel)        # Linux 위로
        self.root.bind_all("<Button-5>", self._on_mousewheel)        # Linux 아래로
        return self.content

    def _sync_scrollregion(self) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_mousewheel(self, event) -> None:
        # 로그창 위에서 굴릴 때는 로그창이 스크롤되어야 하므로 전체 스크롤은 건너뛴다.
        widget = event.widget
        while widget is not None:
            if widget is getattr(self, "log_box", None):
                return
            widget = getattr(widget, "master", None)
        if event.num == 4:
            self.canvas.yview_scroll(-3, "units")
        elif event.num == 5:
            self.canvas.yview_scroll(3, "units")
        else:
            self.canvas.yview_scroll(-1 * (event.delta // 120) * 3, "units")

    # ───────────── UI 구성 ─────────────
    def _build_widgets(self) -> None:
        parent = self._build_scroll_shell()

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

        guide_row = tk.Frame(api_frame)
        guide_row.grid(row=0, column=2, sticky="w", padx=(10, 0), pady=5)
        self.btn_api_guide = tk.Button(guide_row, text="📖 Gate.io API Key 발급방법", bg="#f39c12", fg="white",
                                       command=self._show_api_guide)
        self.btn_api_guide.pack(side="left")

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

        tk.Label(api_frame, text="텔레그램 봇 토큰:").grid(row=5, column=0, sticky="w")
        self.tg_token_var = tk.StringVar()
        tk.Entry(api_frame, textvariable=self.tg_token_var, width=55, show="*").grid(
            row=5, column=1, columnspan=2, sticky="w", pady=5)

        tk.Label(api_frame, text="텔레그램 Chat ID:").grid(row=6, column=0, sticky="w")
        self.tg_chat_var = tk.StringVar()
        tg_row = tk.Frame(api_frame)
        tg_row.grid(row=6, column=1, columnspan=2, sticky="w", pady=5)
        tk.Entry(tg_row, textvariable=self.tg_chat_var, width=30).pack(side="left")
        tk.Button(tg_row, text="🔍 대화방 ID 찾기", bg="#8e44ad", fg="white",
                  command=self._discover_chats).pack(side="left", padx=(8, 0))
        tk.Button(tg_row, text="✈ 연결 테스트", bg="#2980b9", fg="white",
                  command=self._test_telegram).pack(side="left", padx=(6, 0))
        tk.Label(api_frame,
                 text="※ 여러 곳에 동시 전송하려면 쉼표로 구분해서 넣으세요 (예: 66721231, -1001234567890)."
                      "  그룹방 ID는 -100으로 시작합니다.",
                 fg="#7f8c8d", font=("맑은 고딕", 8)).grid(row=7, column=0, columnspan=3, sticky="w")

        self.save_keys_var = tk.BooleanVar(value=True)
        save_row = tk.Frame(api_frame)
        save_row.grid(row=8, column=0, columnspan=3, sticky="w", pady=(5, 0))
        tk.Checkbutton(save_row, text="API 키 저장 (다음에 자동 입력)", variable=self.save_keys_var,
                       command=self._schedule_save).pack(side="left")
        tk.Button(save_row, text="저장된 키 삭제", command=self._clear_saved_credentials).pack(side="left", padx=(10, 0))
        tk.Label(api_frame, text="⚠️ 체크 시 이 PC에 평문으로 저장됩니다. 본인 개인 PC에서만 사용하세요.",
                 fg="#c0392b", font=("맑은 고딕", 8)).grid(row=9, column=0, columnspan=3, sticky="w", pady=(2, 0))
        tk.Label(api_frame, text="※ 거래소를 바꾸면 그 거래소용으로 저장된 키가 자동으로 불러와집니다.",
                 fg="#7f8c8d", font=("맑은 고딕", 8)).grid(row=10, column=0, columnspan=3, sticky="w")

        self._update_passphrase_visibility()

        # 2. 상세설정
        config_frame = tk.LabelFrame(parent, text=" 상세설정 ", padx=10, pady=10)
        config_frame.pack(fill="x", padx=15, pady=5)

        tk.Label(config_frame, text="레버리지 (1~100배):").grid(row=0, column=0, sticky="w")
        self.leverage_var = tk.StringVar(value=f"{core.LEVERAGE:g}")
        tk.Spinbox(config_frame, from_=1, to=100, increment=1, width=15,
                   textvariable=self.leverage_var).grid(row=0, column=1, sticky="w", pady=5)
        lev_hint = tk.Frame(config_frame)
        lev_hint.grid(row=0, column=2, sticky="w", padx=(8, 0))
        tk.Label(lev_hint, text="※ 10배 추천", fg="#c0392b",
                 font=("맑은 고딕", 9, "bold")).pack(side="left")
        tk.Label(lev_hint, text="(시작할 때 거래소 계좌에 설정됩니다)",
                 fg="#7f8c8d", font=("맑은 고딕", 8)).pack(side="left", padx=(6, 0))

        tk.Label(config_frame, text="포지션 크기 (%):").grid(row=1, column=0, sticky="w")
        self.pos_pct_var = tk.StringVar(value=f"{core.INITIAL_MARGIN_PCT * 100:g}")
        tk.Entry(config_frame, textvariable=self.pos_pct_var, width=18).grid(row=1, column=1, sticky="w", pady=5)
        pos_hint = tk.Frame(config_frame)
        pos_hint.grid(row=1, column=2, sticky="w", padx=(8, 0))
        tk.Label(pos_hint, text="※ 2% 추천", fg="#c0392b",
                 font=("맑은 고딕", 9, "bold")).pack(side="left")
        tk.Label(pos_hint, text="(예: 잔고 100 × 10배 × 2% ≈ 20 USDT 진입)",
                 fg="#7f8c8d", font=("맑은 고딕", 8)).pack(side="left", padx=(6, 0))

        tk.Label(config_frame, text="익절 (%):").grid(row=2, column=0, sticky="w")
        self.tp_pct_var = tk.StringVar(value=f"{core.TP_PCT * 100:g}")
        tk.Entry(config_frame, textvariable=self.tp_pct_var, width=18).grid(row=2, column=1, sticky="w", pady=5)
        tk.Label(config_frame, text="(평단가 대비 이만큼 유리하면 전량 익절)",
                 fg="#7f8c8d", font=("맑은 고딕", 8)).grid(row=2, column=2, sticky="w", padx=(8, 0))

        tk.Label(config_frame, text="물타기 간격 (%):").grid(row=3, column=0, sticky="w")
        self.step_pct_var = tk.StringVar(value=f"{core.STEP_TRIGGER_PCT * 100:g}")
        tk.Entry(config_frame, textvariable=self.step_pct_var, width=18).grid(row=3, column=1, sticky="w", pady=5)
        tk.Label(config_frame, text=f"(이만큼 불리해질 때마다 1→2→4→8배 추가, 최대 {core.MAX_STEPS}단계)",
                 fg="#7f8c8d", font=("맑은 고딕", 8)).grid(row=3, column=2, sticky="w", padx=(8, 0))

        tk.Label(config_frame, text="손절 (%):").grid(row=4, column=0, sticky="w")
        self.sl_pct_var = tk.StringVar(value=f"{core.STOP_LOSS_PCT * 100:g}")
        tk.Entry(config_frame, textvariable=self.sl_pct_var, width=18).grid(row=4, column=1, sticky="w", pady=5)
        tk.Label(config_frame, text="(평단가 대비 이만큼 불리하면 단계 무관 전량 손절)",
                 fg="#7f8c8d", font=("맑은 고딕", 8)).grid(row=4, column=2, sticky="w", padx=(8, 0))

        self.verbose_var = tk.BooleanVar(value=True)
        tk.Checkbutton(config_frame, text="상세 로그 (진입 금액 계산식·주문 실행 내역까지 표시)",
                       variable=self.verbose_var, command=self._apply_log_level).grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(5, 0))

        # 모든 입력값은 타이핑할 때마다 자동 저장되어, 프로그램을 강제 종료해도 다음 실행 시 그대로 남는다.
        for var in (self.api_key_var, self.api_secret_var, self.passphrase_var,
                    self.tg_token_var, self.tg_chat_var,
                    self.leverage_var, self.pos_pct_var,
                    self.tp_pct_var, self.step_pct_var, self.sl_pct_var):
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

        history_btn_frame = tk.Frame(parent)
        history_btn_frame.pack(fill="x", padx=15, pady=(0, 5))
        tk.Button(history_btn_frame, text="📊 매매 기록 보기", bg="#34495e", fg="white",
                  font=("맑은 고딕", 10, "bold"), command=self._show_trade_history).pack(
            side="left", fill="x", expand=True)
        tk.Button(history_btn_frame, text="⟳ 매매상태 초기화", bg="#7f8c8d", fg="white",
                  font=("맑은 고딕", 10, "bold"), command=self._on_reset_state).pack(
            side="left", padx=(6, 0))

        # 4. 실시간 로그 창
        log_frame = tk.LabelFrame(parent, text=" 실시간 매매 로그 및 상태 ", padx=10, pady=10)
        log_frame.pack(fill="both", expand=True, padx=15, pady=5)

        self.status_var = tk.StringVar(value="상태: 대기 중")
        tk.Label(log_frame, textvariable=self.status_var, anchor="w", justify="left",
                 font=("맑은 고딕", 9, "bold")).pack(fill="x", pady=(0, 5))

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
        # 여기서 예외가 새어 나가면 after 루프가 끊겨 로그창이 영영 비어버린다.
        # (예: 화면 파일만 최신이고 엔진 파일이 구버전이라 없는 값을 참조하는 경우)
        while not self.log_queue.empty():
            line = self.log_queue.get_nowait()
            try:
                self._append_log(line)
            except Exception:
                try:
                    self.log_box.configure(state="normal")
                    self.log_box.insert("end", line + "\n")
                    self.log_box.see("end")
                    self.log_box.configure(state="disabled")
                except Exception:
                    pass
        # 매매 스레드가 요청한 화면 갱신을 메인 스레드에서 실행한다.
        while not self.ui_queue.empty():
            try:
                self.ui_queue.get_nowait()()
            except Exception as e:
                core.logger.debug("화면 갱신 처리 실패: %s", e)
        self.root.after(200, self._poll_log_queue)

    def _run_on_ui(self, fn) -> None:
        """매매 스레드에서 화면 갱신이 필요할 때 사용(메인 스레드에서 실행되도록 큐에 넣는다)."""
        self.ui_queue.put(fn)

    def _append_log(self, text: str) -> None:
        self.log_box.configure(state="normal")
        # 시세/상태 표시줄(하트비트)은 매번 새로 쌓지 않고 직전 줄을 갈아끼운다.
        # 30초마다 한 줄씩 쌓이면 정작 중요한 진입/청산 기록이 위로 밀려나기 때문이다.
        is_beat = getattr(core, "HEARTBEAT_MARK", "\u23f1") in text
        if is_beat and self._last_line_is_heartbeat:
            self.log_box.delete("end-2l", "end-1l")
        self.log_box.insert("end", text + "\n")
        self._last_line_is_heartbeat = is_beat
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
        self._flush_pending_save()   # 직전 거래소용으로 입력한 값을 먼저 저장(유실 방지)
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
        # 칸이 늘거나 줄어든 만큼 창 크기도 다시 맞춘다(최초 구성 중에는 아직 창이 없으므로 건너뜀).
        if self._window_fitted:
            self._fit_window_to_content()

    def _show_api_guide(self) -> None:
        name = self.exchange_var.get()
        guide = core.EXCHANGE_API_GUIDES.get(name, "안내 준비 중입니다.")
        ref = core.EXCHANGE_REFERRALS.get(name)

        win = tk.Toplevel(self.root)
        win.title(f"{name} API Key 발급 방법")
        win.geometry("660x660")
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

        if ref:
            ref_row = tk.Frame(win)
            ref_row.pack(pady=(0, 6))
            tk.Button(ref_row, text="🔗 가입 링크 열기", bg="#f39c12", fg="white",
                      font=("맑은 고딕", 10, "bold"),
                      command=lambda: self._open_link(ref["link"])).pack(side="left", padx=4)
            tk.Button(ref_row, text="📋 레퍼럴 코드 복사", bg="#34495e", fg="white",
                      font=("맑은 고딕", 10, "bold"),
                      command=lambda: self._copy_to_clipboard(ref["code"], "레퍼럴 코드")).pack(side="left", padx=4)
            tk.Button(ref_row, text="📋 가입 링크 복사", bg="#34495e", fg="white",
                      font=("맑은 고딕", 10, "bold"),
                      command=lambda: self._copy_to_clipboard(ref["link"], "가입 링크")).pack(side="left", padx=4)

        tk.Button(win, text="닫기", command=win.destroy, width=12).pack(pady=(0, 12))

    def _open_link(self, url: str) -> None:
        try:
            webbrowser.open(url)
        except Exception as e:
            messagebox.showerror("오류", f"브라우저를 열지 못했습니다.\n주소를 직접 복사해서 이용해주세요.\n\n{e}")

    def _copy_to_clipboard(self, text: str, label: str) -> None:
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update()  # 프로그램을 닫아도 클립보드에 남도록 반영
            messagebox.showinfo("복사 완료", f"{label}를 복사했습니다.\n\n{text}")
        except Exception as e:
            messagebox.showerror("오류", f"복사하지 못했습니다.\n\n{e}")

    def _show_caution(self) -> None:
        win = tk.Toplevel(self.root)
        win.title("⚠️ 주의사항")
        win.geometry("680x680")

        tk.Label(win, text="⚠️ 사용 전 주의사항", font=("맑은 고딕", 14, "bold"),
                 fg="#c0392b").pack(pady=(12, 2))
        tk.Label(win, text="레버리지 거래는 원금 전액을 잃을 수 있습니다. 끝까지 읽어주세요.",
                 font=("맑은 고딕", 9), fg="#7f8c8d").pack(pady=(0, 8))

        text_frame = tk.Frame(win)
        text_frame.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        sb = tk.Scrollbar(text_frame)
        sb.pack(side="right", fill="y")
        txt = tk.Text(text_frame, wrap="word", yscrollcommand=sb.set, font=("맑은 고딕", 10),
                      padx=8, pady=8)
        txt.pack(side="left", fill="both", expand=True)
        sb.config(command=txt.yview)
        txt.insert("1.0", core.CAUTION_TEXT)
        txt.config(state="disabled")

        tk.Button(win, text="확인했습니다", command=win.destroy, width=16,
                  bg="#c0392b", fg="white", font=("맑은 고딕", 10, "bold")).pack(pady=(0, 12))

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

        # 거래소와 무관한 설정(텔레그램/전략값/상세로그)은 프로그램을 처음 켤 때만 불러온다.
        # 거래소를 바꿀 때마다 다시 덮어쓰면, 방금 입력하고 아직 저장 안 된 값이 날아갈 수 있다.
        if exchange_name is not None:
            return

        for key, var in (("leverage", self.leverage_var),
                         ("pos_pct", self.pos_pct_var),
                         ("tp_pct", self.tp_pct_var),
                         ("step_pct", self.step_pct_var),
                         ("sl_pct", self.sl_pct_var),
                         ("tg_token", self.tg_token_var),
                         ("tg_chat", self.tg_chat_var)):
            value = data.get(key)
            if value not in (None, ""):
                var.set(str(value))
        if data.get("verbose") is not None:
            self.verbose_var.set(bool(data["verbose"]))

    def _save_credentials(self, exchange_name: str = None) -> None:
        try:
            data = self._read_config()
            name = exchange_name or self.exchange_var.get()
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
                "tp_pct": self.tp_pct_var.get().strip(),
                "step_pct": self.step_pct_var.get().strip(),
                "sl_pct": self.sl_pct_var.get().strip(),
                "tg_token": self.tg_token_var.get().strip(),
                "tg_chat": self.tg_chat_var.get().strip(),
                "verbose": bool(self.verbose_var.get()),
            })
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception as e:
            self._log(f"설정 저장 실패: {e}")

    def _schedule_save(self, *_args) -> None:
        """입력칸이 바뀔 때마다 호출되며, 타이핑이 잠시 멈춘 뒤(500ms) 한 번만 저장한다.

        어느 거래소용 입력인지 함께 기억해둔다. 저장이 실행되기 전에 거래소를 바꾸면
        예약이 취소되면서 방금 입력한 키가 사라지므로, 전환 직전에 _flush_pending_save로
        먼저 반영해야 한다.
        """
        if self._save_after_id is not None:
            self.root.after_cancel(self._save_after_id)
        self._pending_save_exchange = self.exchange_var.get()
        self._save_after_id = self.root.after(500, self._flush_pending_save)

    def _flush_pending_save(self) -> None:
        """예약된 저장이 있으면 지금 즉시 반영한다(거래소 전환/종료 시 유실 방지)."""
        if self._save_after_id is not None:
            try:
                self.root.after_cancel(self._save_after_id)
            except Exception:
                pass
            self._save_after_id = None
        target = self._pending_save_exchange
        self._pending_save_exchange = None
        if target:
            self._save_credentials(target)

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

    # ───────────── 개인용 전용 기능 ─────────────
    def _apply_log_level(self) -> None:
        """상세 로그 체크 시 내부 동작(진입 금액 계산식·주문 실행 내역 등)까지 화면에 표시한다."""
        level = logging.DEBUG if self.verbose_var.get() else logging.INFO
        core.logger.setLevel(level)
        for h in core.logger.handlers:
            h.setLevel(level)
        self._log(f"상세 로그 {'켜짐' if self.verbose_var.get() else '꺼짐'}")

    def _discover_chats(self) -> None:
        """봇이 최근에 본 대화방(개인/그룹)의 Chat ID를 찾아서 보여준다.

        그룹방 ID는 눈으로 확인할 방법이 마땅치 않아서, getUpdates에 잡힌 대화방을
        긁어 목록으로 보여주고 바로 입력칸에 넣을 수 있게 한다.
        """
        token = self.tg_token_var.get().strip()
        if not token:
            messagebox.showerror("입력 오류", "먼저 텔레그램 봇 토큰을 입력하세요.")
            return
        try:
            resp = requests.get(f"https://api.telegram.org/bot{token}/getUpdates",
                                params={"limit": 100}, timeout=10)
            body = resp.json()
        except Exception as e:
            self._log(f"대화방 조회 오류: {e}")
            messagebox.showerror("조회 실패", f"텔레그램 서버에 연결하지 못했습니다.\n\n{e}")
            return
        if not body.get("ok"):
            desc = body.get("description")
            self._log(f"대화방 조회 실패: {desc}")
            messagebox.showerror("조회 실패", f"텔레그램이 거부했습니다.\n\n{desc}\n\n봇 토큰이 맞는지 확인하세요.")
            return

        found = {}
        for upd in body.get("result", []):
            for key in ("message", "channel_post", "edited_message", "my_chat_member"):
                chat = (upd.get(key) or {}).get("chat")
                if not chat:
                    continue
                title = chat.get("title") or " ".join(
                    x for x in (chat.get("first_name"), chat.get("last_name")) if x
                ) or chat.get("username") or "(이름 없음)"
                kind = {"private": "개인 DM", "group": "그룹", "supergroup": "그룹",
                        "channel": "채널"}.get(chat.get("type"), chat.get("type"))
                found[str(chat.get("id"))] = f"{kind} · {title}"

        if not found:
            messagebox.showinfo(
                "찾은 대화방 없음",
                "봇이 최근에 받은 메시지가 없습니다.\n\n"
                "1) 봇을 그룹방에 초대하세요.\n"
                "2) 그룹방에서 아무 메시지나 한 번 보내세요 (또는 /start).\n"
                "3) 다시 이 버튼을 누르세요.\n\n"
                "※ 그룹에서 봇이 메시지를 못 보는 경우: @BotFather → /setprivacy → Disable\n"
                "※ 봇을 그룹 관리자로 올리면 확실합니다.",
            )
            return

        lines = "\n".join(f"  {cid}    {name}" for cid, name in found.items())
        self._log(f"대화방 {len(found)}곳 발견")
        for cid, name in found.items():
            self._log(f"   {cid}  {name}")
        if messagebox.askyesno(
            "찾은 대화방",
            f"{lines}\n\n이 ID들을 모두 입력칸에 넣을까요?\n"
            "(아니오를 누르면 로그에만 남습니다. 원하는 것만 직접 골라 넣으세요.)",
        ):
            self.tg_chat_var.set(", ".join(found.keys()))
            self._schedule_save()

    def _test_telegram(self) -> None:
        """입력한 토큰/Chat ID(들)로 실제 메시지를 보내 연결을 확인한다."""
        token = self.tg_token_var.get().strip()
        chats = core.parse_chat_ids(self.tg_chat_var.get())
        if not token or not chats:
            messagebox.showerror("입력 오류", "텔레그램 봇 토큰과 Chat ID를 모두 입력하세요.")
            return
        ok_list, fail_list = [], []
        for chat in chats:
            try:
                resp = requests.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    data={"chat_id": chat,
                          "text": "[개인용 봇] 텔레그램 연결 테스트입니다. 이 메시지가 보이면 정상입니다."},
                    timeout=10,
                )
                body = resp.json()
            except Exception as e:
                fail_list.append((chat, f"연결 오류: {e}"))
                continue
            if body.get("ok"):
                ok_list.append(chat)
            else:
                fail_list.append((chat, body.get("description") or "알 수 없는 오류"))

        for chat in ok_list:
            self._log(f"텔레그램 전송 성공: {chat}")
        for chat, why in fail_list:
            self._log(f"텔레그램 전송 실패: {chat} — {why}")

        if not fail_list:
            messagebox.showinfo("성공", f"{len(ok_list)}곳으로 테스트 메시지를 보냈습니다.\n앱에서 확인해보세요.")
        else:
            detail = "\n".join(f"  · {c} → {w}" for c, w in fail_list)
            messagebox.showerror(
                "일부 전송 실패",
                (f"성공 {len(ok_list)}곳 / 실패 {len(fail_list)}곳\n\n{detail}\n\n"
                 "· 개인 DM이면 그 봇에게 먼저 /start 를 보냈는지\n"
                 "· 그룹방이면 봇을 그룹에 초대했는지, ID가 -100으로 시작하는지\n"
                 "  확인하세요."),
            )

    def _on_reset_state(self) -> None:
        """저장된 진행 상태를 지워 다음 시작 시 포지션 0에서 새로 시작한다."""
        if self.worker_thread is not None and self.worker_thread.is_alive():
            messagebox.showwarning("실행 중", "먼저 '■ 정지'를 누른 뒤 초기화하세요.")
            return
        name = self.exchange_var.get()
        if not messagebox.askyesno(
            "매매상태 초기화",
            "저장된 매매 진행 상태를 삭제하고 다음 시작 시 처음부터 진행합니다.\n\n"
            f"⚠️ {name}에 실제로 열려 있는 포지션은 삭제되지 않습니다.\n"
            f"포지션이 남아 있으면 {name}에서 직접 청산한 뒤 초기화하세요.\n\n계속할까요?",
        ):
            return
        try:
            if os.path.exists(core.STATE_PATH):
                os.remove(core.STATE_PATH)
                self._log("저장된 매매 상태를 초기화했습니다.")
            else:
                self._log("저장된 매매 상태가 없습니다(이미 초기 상태).")
        except Exception as e:
            self._log(f"상태 초기화 실패: {e}")

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
            tp_pct = self._parse_pct(self.tp_pct_var.get(), "익절 %")
            step_pct = self._parse_pct(self.step_pct_var.get(), "물타기 간격 %")
            sl_pct = self._parse_pct(self.sl_pct_var.get(), "손절 %")
        except ValueError as e:
            messagebox.showerror("입력 오류", f"포지션/익절/물타기/손절 %는 0보다 큰 숫자여야 합니다.\n({e})")
            return

        if sl_pct <= step_pct:
            messagebox.showerror(
                "입력 오류",
                f"손절 %({sl_pct*100:g}%)는 물타기 간격 %({step_pct*100:g}%)보다 커야 합니다.\n"
                "그렇지 않으면 물타기 없이 바로 손절됩니다.",
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
        core.TELEGRAM_BOT_TOKEN = self.tg_token_var.get().strip()
        core.TELEGRAM_CHAT_ID = self.tg_chat_var.get().strip()

        self._save_credentials()
        self._apply_log_level()
        self._log(
            f"설정 | {exchange_name} | 레버리지 {leverage}배 | 포지션 {pos_pct*100:g}% | "
            f"익절 {tp_pct*100:g}% | 물타기 {step_pct*100:g}% | 손절 {sl_pct*100:g}%"
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
            bot = core.HedgedMartingaleBot(broker, notifier, mode_label="LIVE", state_path=core.STATE_PATH,
                                           on_trade_closed=self._on_trade_closed)
            market_data = core.PublicMarketData(exchange_name)
        except Exception as e:
            core.logger.debug("계좌 연결 실패: %s", e)
            self._log("[오류] 계좌 연결에 실패했습니다. API Key/Secret과 인터넷 연결을 확인해주세요.")
            self._run_on_ui(self._set_stopped_ui)
            return

        self.bot = bot
        self.exchange_label = exchange_name
        self._run_on_ui(self._update_position_status)
        self._log(f"{exchange_name} 계좌에 연결되었습니다.")

        if core.TELEGRAM_BOT_TOKEN and core.TELEGRAM_CHAT_ID:
            notifier.send(f"[개인용 봇] {exchange_name} 자동매매를 시작했습니다.")
        else:
            self._log("텔레그램 미설정 — 알림 없이 진행합니다(화면 로그에는 계속 표시됨).")

        try:
            bot.run_forever(market_data, poll_sec=core.POLL_SEC, stop_event=self.stop_event)
        except Exception as e:
            # 예기치 못한 오류로 매매 스레드가 끝나더라도 화면이 '매매 중'에 멈춰 있으면
            # 사용자가 다시 시작할 수 없으므로, 반드시 안내하고 버튼 상태를 되돌린다.
            core.logger.debug("매매 루프 예외 종료: %s", e)
            self._log("[오류] 자동매매가 예기치 않게 중단되었습니다. 거래소에 열린 포지션을 확인한 뒤 다시 시작해주세요.")
        finally:
            self.bot = None
            self._log("자동매매가 정지되었습니다.")
            self._run_on_ui(self._set_stopped_ui)

    def _update_position_status(self) -> None:
        """상태줄에 현재 포지션을 USDT 기준으로 보여주고, 1~4차 진입가/금액을 함께 표시한다.

        아직 안 온 단계는 봇이 실제로 쓸 규칙 그대로 굴려서 계산한 값이라, 여기 찍힌
        가격에 도달하면 그 금액으로 진입한다.
        """
        bot = self.bot
        if bot is None:
            return

        def describe(module, label) -> str:
            if not module.in_position:
                return f"{label}  없음"
            invested = module.total_qty * module.avg_price
            head = (f"{label}  {module.step}차 진입 중 | 투입 {invested:,.0f} USDT | "
                    f"평단 {module.avg_price:,.2f}")
            steps = []
            for item in core.martingale_ladder(module.side, module.fills):
                mark = "✓" if item["done"] else " "
                steps.append(f"{mark}{item['stage']}차 {item['price']:,.0f} ({item['usdt']:,.0f})")
            return head + "\n        " + "   ".join(steps)

        self.status_var.set(
            f"상태: 매매 중 ({self.exchange_label})\n"
            + describe(bot.long, "롱 ") + "\n"
            + describe(bot.short, "숏 ")
        )
        self._status_after_id = self.root.after(2000, self._update_position_status)

    def _on_stop_clicked(self) -> None:
        self.status_var.set("상태: 정지 중...")
        self.stop_event.set()

    # ───────────── 매매 기록 ─────────────
    def _load_trade_history(self) -> None:
        self.trade_history = []
        if not os.path.exists(core.TRADE_LOG_PATH):
            return
        try:
            with open(core.TRADE_LOG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self.trade_history = data
        except Exception as e:
            self._log(f"매매 기록을 불러오지 못했습니다: {e}")

    def _save_trade_history(self) -> None:
        try:
            with open(core.TRADE_LOG_PATH, "w", encoding="utf-8") as f:
                json.dump(self.trade_history, f, ensure_ascii=False)
        except Exception as e:
            self._log(f"매매 기록 저장 실패: {e}")

    def _on_trade_closed(self, trade: dict) -> None:
        """청산될 때마다(매매 스레드에서) 호출된다. 기록에 추가하고 로그에도 한 줄 남긴다."""
        self.trade_history.append(trade)
        self._save_trade_history()
        side_ko = "롱" if trade["side"] == "LONG" else "숏"
        self._log(
            f"📊 {side_ko} 거래완료 | 진입 {trade['entry_price']:,.2f} → 청산 {trade['exit_price']:,.2f} | "
            f"손익 {trade['profit_usdt']:+.2f} USDT ({trade['leveraged_return_pct']:+.2f}%)"
        )

    def _show_trade_history(self) -> None:
        """매매 기록을 별도 창에 표로 보여준다."""
        win = tk.Toplevel(self.root)
        win.title("📊 자동매매 기록")
        win.geometry("900x500")

        summary_frame = tk.Frame(win, pady=8)
        summary_frame.pack(fill="x", padx=10)

        if self.trade_history:
            total = len(self.trade_history)
            wins = sum(1 for t in self.trade_history if t.get("leveraged_return_pct", 0) > 0)
            win_rate = wins / total * 100
            avg_return = sum(t.get("leveraged_return_pct", 0) for t in self.trade_history) / total
            total_profit = sum(t.get("profit_usdt", 0.0) for t in self.trade_history)
            summary_text = (
                f"총 거래 {total}건  |  승률 {win_rate:.1f}% ({wins}승 {total - wins}패)  |  "
                f"건당 평균 수익률 {avg_return:+.2f}%  |  총 수익금 {total_profit:+.2f} USDT"
            )
        else:
            summary_text = "아직 매매 기록이 없습니다."

        tk.Label(summary_frame, text=summary_text, font=("맑은 고딕", 10, "bold")).pack(side="left")
        tk.Button(summary_frame, text="기록 전체 삭제",
                  command=lambda: self._clear_trade_history(win)).pack(side="right")

        columns = ("time", "side", "entry", "exit", "profit", "ret")
        tree = ttk.Treeview(win, columns=columns, show="headings", height=18)
        headers = {"time": "시각", "side": "방향", "entry": "진입가", "exit": "청산가",
                   "profit": "수익금(USDT)", "ret": "수익률%"}
        widths = {"time": 160, "side": 60, "entry": 110, "exit": 110, "profit": 120, "ret": 100}
        for col in columns:
            tree.heading(col, text=headers[col])
            tree.column(col, width=widths[col], anchor="center")

        tree.tag_configure("win", foreground="#1a7a1a")
        tree.tag_configure("loss", foreground="#c0392b")

        for t in reversed(self.trade_history):  # 최신 기록이 위로
            ret = t.get("leveraged_return_pct", 0.0)
            tag = "win" if ret > 0 else "loss"
            tree.insert("", "end", values=(
                t.get("time", "-"), t.get("side", "-"),
                f"{t.get('entry_price', 0):,.2f}", f"{t.get('exit_price', 0):,.2f}",
                f"{t.get('profit_usdt', 0.0):+.2f}", f"{ret:+.2f}",
            ), tags=(tag,))

        scrollbar = ttk.Scrollbar(win, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=(0, 10))
        scrollbar.pack(side="right", fill="y", pady=(0, 10))

    def _clear_trade_history(self, win: tk.Toplevel) -> None:
        if not messagebox.askyesno("확인", "저장된 매매 기록을 전부 삭제하시겠습니까? 되돌릴 수 없습니다."):
            return
        self.trade_history = []
        self._save_trade_history()
        win.destroy()
        self._log("매매 기록을 전부 삭제했습니다.")
        messagebox.showinfo("완료", "매매 기록을 삭제했습니다.")

    def _set_stopped_ui(self) -> None:
        if self._status_after_id is not None:
            self.root.after_cancel(self._status_after_id)
            self._status_after_id = None
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.combo_exchange.config(state="readonly")
        self.status_var.set("상태: 대기 중")

    def _on_close(self) -> None:
        self._flush_pending_save()
        self._save_credentials()
        self.stop_event.set()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    root.withdraw()
    if not check_core_compatible(root):
        root.destroy()
        return
    root.deiconify()
    PersonalTradingGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
