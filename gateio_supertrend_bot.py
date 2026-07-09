import tkinter as tk
from tkinter import messagebox, ttk
import threading
import ccxt
import time
import pandas as pd
import json
import os
import sys
from license_client_code import ensure_license_active, start_periodic_recheck


EXCHANGE_OPTIONS = {
    "Gate.io": {"ccxt_ids": ("gate", "gateio"), "options": {"defaultType": "swap"}, "needs_passphrase": False},
    "Binance": {"ccxt_ids": ("binance",), "options": {"defaultType": "future"}, "needs_passphrase": False},
    "Bybit":   {"ccxt_ids": ("bybit",), "options": {"defaultType": "swap", "subType": "linear"}, "needs_passphrase": False},
    "OKX":     {"ccxt_ids": ("okx",), "options": {"defaultType": "swap"}, "needs_passphrase": True},
    "Bitget":  {"ccxt_ids": ("bitget",), "options": {"defaultType": "swap"}, "needs_passphrase": True},
}

def _make_save_warning(needs_passphrase):
    if needs_passphrase:
        return (
            "\n\n⚠️⚠️⚠️ 발급 완료 즉시, API Key / Secret Key / Passphrase 3가지를 "
            "메모장(또는 안전한 곳)에 반드시 복사해서 저장하세요!\n"
            "Secret Key와 Passphrase는 이 화면을 벗어나면 다시는 확인할 수 없고, "
            "분실 시 API Key를 처음부터 새로 발급받아야 합니다."
        )
    return (
        "\n\n⚠️⚠️⚠️ 발급 완료 즉시, API Key / Secret Key를 메모장(또는 안전한 곳)에 반드시 복사해서 저장하세요!\n"
        "Secret Key는 이 화면을 벗어나면 다시는 확인할 수 없고, 분실 시 API Key를 처음부터 새로 발급받아야 합니다."
    )

EXCHANGE_API_GUIDES = {
    "Gate.io": (
        "1. Gate.io 로그인 → 우측 상단 프로필 아이콘 클릭 → 'API Management(API 관리)' 이동\n"
        "2. 'Create New Key(새 키 생성)' 클릭\n"
        "3. API Key Type은 'API v4 Key' 선택\n"
        "4. Permissions(권한)에서 'Perpetual Futures(무기한 선물)' 체크 + 'Read And Write' 권한 부여\n"
        "5. (선택) IP 화이트리스트 등록으로 보안 강화\n"
        "6. 자금 비밀번호 입력 + 2FA(구글 인증 등) 인증\n"
        "7. API Key, Secret Key 발급 완료\n\n"
        "※ 주의: 선물 지갑에 USDT를 한 번도 이체한 적이 없으면 'Perpetual Futures' 권한이 있어도 "
        "'USER_NOT_FOUND' 오류가 날 수 있습니다. 현물→선물 지갑으로 소액이라도 먼저 이체해두세요."
        + _make_save_warning(False)
    ),
    "Binance": (
        "1. Binance 로그인 → 우측 상단 프로필 아이콘 → 'API Management' 이동\n"
        "2. API 이름 입력 후 'Create' 클릭\n"
        "3. 이메일 인증 및 2FA(OTP) 인증 완료\n"
        "4. API Key, Secret Key 발급 (Secret Key는 이 화면에서만 표시됨)\n"
        "5. 'Edit restrictions' 클릭 → 'Enable Futures' 체크\n\n"
        "※ 주의: 선물 계좌를 먼저 개설(활성화)해두지 않은 상태에서 만든 키는 'Enable Futures' 항목 자체가 "
        "없거나 선택할 수 없습니다. 반드시 선물 계좌 개설 후 키를 발급하세요."
        + _make_save_warning(False)
    ),
    "Bybit": (
        "1. Bybit 로그인 → 프로필 아이콘 → 'API' 클릭\n"
        "2. 'API Management' → 'Create New Key' → 'System-generated API Keys' 선택\n"
        "3. 용도(API Transaction) 선택 및 이름 입력\n"
        "4. 권한에서 'Contract(파생상품/Derivatives) - Orders & Positions' 체크\n"
        "5. (선택) IP 제한 설정\n"
        "6. 2FA 인증 후 생성\n\n"
        "※ 참고: Bybit는 최근 통합계좌(UTA) 방식이라 선물 지갑에 자금이 있어야 잔고가 정상 조회됩니다."
        + _make_save_warning(False)
    ),
    "OKX": (
        "1. OKX 로그인 → 프로필 아이콘 → 'API' 또는 'API Key' 메뉴 이동\n"
        "2. 'Create API Key' 클릭\n"
        "3. API 이름 입력, 'Passphrase(비밀번호)' 설정 — API Key/Secret Key와는 별개의 항목입니다\n"
        "4. Permissions(권한)에서 'Trade' 체크\n"
        "5. 2FA 인증 후 생성\n\n"
        "※ 중요: OKX는 API Key, Secret Key 외에 'Passphrase'까지 총 3개의 값이 필요합니다. "
        "이 화면 아래 'Passphrase' 입력칸에도 반드시 함께 입력해주세요."
        + _make_save_warning(True)
    ),
    "Bitget": (
        "1. Bitget 로그인 → 프로필 아이콘 → 'API Management' 이동\n"
        "2. 'Create New API' 클릭\n"
        "3. 'Notes(이름)' 입력, 'Passphrase' 설정 (영문/숫자 8자 이상, 특수문자 사용 불가)\n"
        "4. 권한에서 'Read-write' + 'Futures(선물) - Orders & Holdings' 체크\n"
        "5. 2FA 인증 후 생성\n\n"
        "※ 중요: Bitget도 API Key, Secret Key 외에 'Passphrase'까지 총 3개의 값이 필요합니다. "
        "이 화면 아래 'Passphrase' 입력칸에도 반드시 함께 입력해주세요. Passphrase를 잊으면 키를 재발급해야 합니다."
        + _make_save_warning(True)
    ),
}


def _get_exchange_class(exchange_name):
    """선택한 거래소에 맞는 ccxt 클래스를 반환한다.
    거래소별로 ccxt 버전에 따라 클래스 이름이 다를 수 있어(예: Gate.io의 gate/gateio) 여러 후보를 순서대로 확인한다."""
    import ccxt
    spec = EXCHANGE_OPTIONS.get(exchange_name)
    if spec is None:
        raise RuntimeError(f"지원하지 않는 거래소입니다: {exchange_name}")
    for cid in spec["ccxt_ids"]:
        cls = getattr(ccxt, cid, None)
        if cls is not None:
            return cls
    raise RuntimeError(
        f"ccxt 라이브러리에서 {exchange_name} 지원을 찾을 수 없습니다. 'pip install -U ccxt'로 업데이트하세요."
    )



CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".gateio_supertrend_bot_config.json")
TRADE_LOG_PATH = os.path.join(os.path.expanduser("~"), ".gateio_supertrend_bot_trades.json")

FIXED_TIMEFRAME = "15m"  # 15분봉 고정

# ─────────────────────────────────────────────────────────
# 배포판 고정 전략 설정 (화면에는 노출되지 않고 내부적으로만 사용됨)
# 값을 바꾸고 싶으면 여기서 직접 수정하세요.
# ─────────────────────────────────────────────────────────
FIXED_ST_PERIOD = 10          # SuperTrend Period
FIXED_ST_MULTIPLIER = 3       # SuperTrend Multiplier
FIXED_ATR_PERIOD = 22         # ATR 트레일링 스탑 기간
FIXED_ATR_MULTIPLIER = 3      # ATR 트레일링 스탑 배수
FIXED_POLL_SEC = 10           # 점검 주기 (초)


class GateioProSuperTrendBot:
    def __init__(self, root):
        self.root = root
        self.root.title("멀티거래소 BTC 선물 Pro 자동매매 v6.0")
        self.root.geometry("720x880")
        self.root.minsize(680, 580)
        self.root.resizable(True, True)

        self.is_running = False
        self.exchange = None
        self.symbol = 'BTC/USDT:USDT'

        self.selected_tf = FIXED_TIMEFRAME
        self.trade_history = []
        self.load_trade_history()

        self.create_scrollable_container()
        self.create_widgets()
        self.load_saved_credentials()
        self.log("ℹ️ 프로그램이 시작되었습니다. API Key 입력 후 '자동매매 시작' 버튼을 눌러주세요.")

    def create_scrollable_container(self):
        """창을 줄여도 스크롤(휠/스크롤바)로 전체 내용을 볼 수 있도록 캔버스로 감싼다."""
        container = tk.Frame(self.root)
        container.pack(fill="both", expand=True)

        self.main_canvas = tk.Canvas(container, highlightthickness=0)
        main_scrollbar = tk.Scrollbar(container, orient="vertical", command=self.main_canvas.yview)
        self.scrollable_frame = tk.Frame(self.main_canvas)

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.main_canvas.configure(scrollregion=self.main_canvas.bbox("all"))
        )

        self.canvas_window = self.main_canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.main_canvas.configure(yscrollcommand=main_scrollbar.set)

        # 캔버스 폭이 바뀌면 내부 프레임 폭도 같이 맞춰서, 가로로는 안 잘리고 내용이 꽉 차게 한다
        self.main_canvas.bind(
            "<Configure>",
            lambda e: self.main_canvas.itemconfig(self.canvas_window, width=e.width)
        )

        self.main_canvas.pack(side="left", fill="both", expand=True)
        main_scrollbar.pack(side="right", fill="y")

        # 마우스 휠로 스크롤 (Windows/Mac)
        def _on_mousewheel(event):
            self.main_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self.main_canvas.bind_all("<MouseWheel>", _on_mousewheel)

    # ------------------------- UI -------------------------
    def create_widgets(self):
        parent = self.scrollable_frame

        # 1. API 설정
        api_frame = tk.LabelFrame(parent, text=" 거래소 / API 설정 ", padx=10, pady=10)
        api_frame.pack(fill="x", padx=15, pady=5)

        tk.Label(api_frame, text="거래소:").grid(row=0, column=0, sticky="w")
        self.var_exchange = tk.StringVar(value="Gate.io")
        self.combo_exchange = ttk.Combobox(
            api_frame, textvariable=self.var_exchange,
            values=list(EXCHANGE_OPTIONS.keys()), state="readonly", width=15
        )
        self.combo_exchange.grid(row=0, column=1, sticky="w", pady=5)
        self.combo_exchange.bind("<<ComboboxSelected>>", self.on_exchange_changed)

        self.btn_api_guide = tk.Button(api_frame, text="📖 Gate.io API Key 발급방법", bg="#f39c12", fg="white",
                  command=self.show_api_guide)
        self.btn_api_guide.grid(row=0, column=2, sticky="w", padx=(10, 0), pady=5)

        tk.Label(api_frame, text="API Key:").grid(row=1, column=0, sticky="w")
        self.entry_api_key = tk.Entry(api_frame, width=55, show="*")
        self.entry_api_key.grid(row=1, column=1, columnspan=2, sticky="w", pady=5)

        tk.Label(api_frame, text="Secret Key:").grid(row=2, column=0, sticky="w")
        self.entry_secret_key = tk.Entry(api_frame, width=55, show="*")
        self.entry_secret_key.grid(row=2, column=1, columnspan=2, sticky="w", pady=5)

        self.label_passphrase = tk.Label(api_frame, text="Passphrase:")
        self.entry_passphrase = tk.Entry(api_frame, width=55, show="*")
        self.label_passphrase.grid(row=3, column=0, sticky="w")
        self.entry_passphrase.grid(row=3, column=1, columnspan=2, sticky="w", pady=5)
        self.label_passphrase_note = tk.Label(
            api_frame, text="※ 이 거래소는 API Key/Secret Key 외에 Passphrase도 필요합니다.",
            fg="#e67e22", font=("맑은 고딕", 8))
        self.label_passphrase_note.grid(row=4, column=0, columnspan=3, sticky="w")

        self.var_save_keys = tk.BooleanVar(value=True)
        save_row = tk.Frame(api_frame)
        save_row.grid(row=5, column=0, columnspan=3, sticky="w", pady=(5, 0))
        tk.Checkbutton(save_row, text="API 키 저장 (다음에 자동 입력)", variable=self.var_save_keys).pack(side="left")
        tk.Button(save_row, text="저장된 키 삭제", command=self.clear_saved_credentials).pack(side="left", padx=(10, 0))
        tk.Label(api_frame, text="⚠️ 체크 시 이 PC에 평문으로 저장됩니다. 본인 개인 PC에서만 사용하세요.",
                 fg="#c0392b", font=("맑은 고딕", 8)).grid(row=6, column=0, columnspan=3, sticky="w", pady=(2, 0))
        tk.Label(api_frame, text="※ 거래소마다 API 키가 다릅니다. 거래소를 바꾸면 그 거래소용으로 저장된 키가 자동으로 불러와집니다.",
                 fg="#7f8c8d", font=("맑은 고딕", 8)).grid(row=7, column=0, columnspan=3, sticky="w")

        self.update_passphrase_visibility()

        config_frame = tk.LabelFrame(parent, text=" 상세설정 ", padx=10, pady=10)
        config_frame.pack(fill="x", padx=15, pady=5)

        tk.Label(config_frame, text="레버리지 (1~50배):").grid(row=0, column=0, sticky="w")
        self.spin_leverage = tk.Spinbox(config_frame, from_=1, to=50, increment=1, width=15)
        self.spin_leverage.grid(row=0, column=1, sticky="w", pady=5)
        self.spin_leverage.delete(0, "end")
        self.spin_leverage.insert(0, "20")

        tk.Label(config_frame, text="1회 주문 금액 (USDT):").grid(row=3, column=0, sticky="w")
        self.entry_amount = tk.Entry(config_frame, width=18)
        self.entry_amount.insert(0, "1000")
        self.entry_amount.grid(row=3, column=1, sticky="w", pady=5)
        self.label_amount_btc_preview = tk.Label(config_frame, text="(고정 금액 모드에서만 사용됩니다)",
                                                   fg="#7f8c8d", font=("맑은 고딕", 8))
        self.label_amount_btc_preview.grid(row=3, column=2, sticky="w", padx=(8, 0))

        tk.Label(config_frame, text="주문 금액 방식:").grid(row=4, column=0, sticky="w")
        amount_mode_row = tk.Frame(config_frame)
        amount_mode_row.grid(row=4, column=1, columnspan=2, sticky="w", pady=5)
        self.var_amount_mode = tk.StringVar(value="fixed")
        self.radio_fixed = tk.Radiobutton(amount_mode_row, text="고정 금액", variable=self.var_amount_mode,
                       value="fixed", command=self.update_amount_mode_ui)
        self.radio_fixed.pack(side="left")
        self.radio_balance_pct = tk.Radiobutton(amount_mode_row, text="잔고 비율(복리)", variable=self.var_amount_mode,
                       value="balance_pct", command=self.update_amount_mode_ui)
        self.radio_balance_pct.pack(side="left", padx=(10, 0))

        tk.Label(config_frame, text="잔고 사용 비율 (%):").grid(row=5, column=0, sticky="w")
        self.entry_balance_pct = tk.Entry(config_frame, width=18, state="disabled")
        self.entry_balance_pct.insert(0, "80")
        self.entry_balance_pct.grid(row=5, column=1, sticky="w", pady=5)

        self.label_balance_pct_desc = tk.Label(
            config_frame,
            text="(레버리지 적용 후 금액 기준. 예: 잔고 10 USDT × 레버리지 20배 × 50% ≈ 100 USDT 진입)",
            fg="#7f8c8d", font=("맑은 고딕", 8), justify="left", anchor="w")
        self.label_balance_pct_desc.grid(row=6, column=0, columnspan=3, sticky="w", padx=(0, 0))

        # 4. 제어 버튼
        btn_frame = tk.Frame(parent, pady=10)
        btn_frame.pack(fill="x", padx=15)

        self.btn_start = tk.Button(btn_frame, text="▶ 자동매매 시작", bg="#2ecc71", fg="white",
                                    font=("맑은 고딕", 12, "bold"), height=1, command=self.start_bot)
        self.btn_start.pack(side="left", fill="x", expand=True, padx=5)

        self.btn_stop = tk.Button(btn_frame, text="■ 정지", bg="#e74c3c", fg="white",
                                   font=("맑은 고딕", 12, "bold"), height=1, state="disabled", command=self.stop_bot)
        self.btn_stop.pack(side="right", fill="x", expand=True, padx=5)

        history_btn_frame = tk.Frame(parent)
        history_btn_frame.pack(fill="x", padx=15, pady=(0, 5))
        tk.Button(history_btn_frame, text="📊 매매 기록 보기", bg="#34495e", fg="white",
                  font=("맑은 고딕", 10, "bold"), command=self.show_trade_history).pack(fill="x")

        # 5. 실시간 로그 창
        log_frame = tk.LabelFrame(parent, text=" 실시간 매매 로그 및 상태 ", padx=10, pady=10)
        log_frame.pack(fill="both", expand=True, padx=15, pady=5)

        log_inner = tk.Frame(log_frame)
        log_inner.pack(fill="both", expand=True)

        log_scrollbar = tk.Scrollbar(log_inner)
        log_scrollbar.pack(side="right", fill="y")

        self.log_text = tk.Text(log_inner, height=15, width=70, state="disabled", bg="#1e1e1e", fg="#ffffff",
                                 yscrollcommand=log_scrollbar.set, wrap="word")
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scrollbar.config(command=self.log_text.yview)

    # ------------------------- API 키 저장/불러오기 (거래소별) -------------------------
    def load_saved_credentials(self):
        exchange_name = self.var_exchange.get()
        self.entry_api_key.delete(0, "end")
        self.entry_secret_key.delete(0, "end")
        self.entry_passphrase.delete(0, "end")
        try:
            if os.path.exists(CONFIG_PATH):
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)

                if "api_key" in data or "secret_key" in data:
                    # 이전 버전(단일 거래소) 형식 — Gate.io 전용으로 간주해 마이그레이션
                    legacy = {"api_key": data.get("api_key", ""), "secret_key": data.get("secret_key", "")}
                    data = {"Gate.io": legacy}

                entry = data.get(exchange_name, {})
                api_key = entry.get("api_key", "")
                secret_key = entry.get("secret_key", "")
                passphrase = entry.get("passphrase", "")
                if api_key:
                    self.entry_api_key.insert(0, api_key)
                if secret_key:
                    self.entry_secret_key.insert(0, secret_key)
                if passphrase:
                    self.entry_passphrase.insert(0, passphrase)
        except Exception:
            pass

    def save_credentials(self):
        if not self.var_save_keys.get():
            return
        exchange_name = self.var_exchange.get()
        try:
            data = {}
            if os.path.exists(CONFIG_PATH):
                try:
                    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if "api_key" in data or "secret_key" in data:
                        data = {"Gate.io": {"api_key": data.get("api_key", ""), "secret_key": data.get("secret_key", "")}}
                except Exception:
                    data = {}

            data[exchange_name] = {
                "api_key": self.entry_api_key.get().strip(),
                "secret_key": self.entry_secret_key.get().strip(),
                "passphrase": self.entry_passphrase.get().strip(),
            }
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f)
            try:
                os.chmod(CONFIG_PATH, 0o600)
            except Exception:
                pass
        except Exception as e:
            self.log(f"⚠️ API 키 저장 실패: {e}")

    def clear_saved_credentials(self):
        exchange_name = self.var_exchange.get()
        try:
            if os.path.exists(CONFIG_PATH):
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if "api_key" in data or "secret_key" in data:
                    data = {}  # 이전 단일 형식이면 통째로 초기화
                data.pop(exchange_name, None)
                with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                    json.dump(data, f)
            messagebox.showinfo("완료", f"{exchange_name}용으로 저장된 API 키를 삭제했습니다.")
        except Exception as e:
            messagebox.showerror("오류", f"삭제 실패: {e}")

    # ------------------------- 매매 기록 저장/불러오기 -------------------------
    # 기록에 남겨둘 항목만 (시각/방향/진입가/청산가/수익금/수익률) — 나머지는 파일에서도 제거
    TRADE_RECORD_KEEP_KEYS = ('time', 'side', 'entry_price', 'exit_price', 'profit_usdt', 'leveraged_return_pct')

    def load_trade_history(self):
        """이전에 저장된 매매 기록을 불러온다 (프로그램을 껐다 켜도 기록 유지).
        더 이상 쓰지 않는 항목(타임프레임/청산사유/레버리지/가격변동%)이 기존 파일에 남아있으면
        불러오는 김에 정리해서 파일도 함께 갱신한다."""
        try:
            if os.path.exists(TRADE_LOG_PATH):
                with open(TRADE_LOG_PATH, "r", encoding="utf-8") as f:
                    raw = json.load(f)

                cleaned = [
                    {k: rec[k] for k in self.TRADE_RECORD_KEEP_KEYS if k in rec}
                    for rec in raw
                ]
                self.trade_history = cleaned

                if cleaned != raw:
                    self.save_trade_history()  # 정리된 내용을 파일에도 즉시 반영
        except Exception:
            self.trade_history = []

    def save_trade_history(self):
        try:
            with open(TRADE_LOG_PATH, "w", encoding="utf-8") as f:
                json.dump(self.trade_history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.log(f"⚠️ 매매 기록 저장 실패: {e}")

    def record_trade(self, side, entry_price, exit_price, reason, position_size=0.0):
        """청산이 일어날 때마다 매매 기록을 한 줄 추가하고 파일로 저장한다.
        position_size: 청산된 계약 수 (수익금 USDT 계산에 사용)"""
        # 레버리지는 루프 시작 시 저장한 값 사용 (Tkinter 위젯 직접 접근 방지)
        leverage = getattr(self, '_current_leverage', 1)

        # entry_price가 0이면 거래소 응답 이상 — 기록은 남기되 수익률은 N/A 처리
        if entry_price <= 0:
            self.log(f"⚠️ 매매 기록: 진입가 정보 없음 (entry_price=0). 수익률 계산 불가.")
            entry_price = exit_price  # 0% 수익률로 기록 (오기록 방지)

        if side == 'long':
            price_return_pct = (exit_price - entry_price) / entry_price * 100
        else:
            price_return_pct = (entry_price - exit_price) / entry_price * 100

        leveraged_return_pct = price_return_pct * leverage

        # 수익금(USDT) = 포지션 명목가치(계약수 × 계약단위 × 진입가) × 가격변동률
        try:
            contract_size = float(self.exchange.market(self.symbol).get('contractSize') or 0.0001)
        except Exception:
            contract_size = 0.0001
        notional_usdt = position_size * contract_size * entry_price
        profit_usdt = notional_usdt * (price_return_pct / 100.0)

        record = {
            'time': time.strftime("%Y-%m-%d %H:%M:%S"),
            'side': side,
            'entry_price': entry_price,
            'exit_price': exit_price,
            'leveraged_return_pct': round(leveraged_return_pct, 3),
            'profit_usdt': round(profit_usdt, 4),
        }
        self.trade_history.append(record)
        self.save_trade_history()
        self.log(
            f"📝 매매 기록 저장: {side.upper()} {entry_price} → {exit_price} | {reason} | "
            f"가격변동 {price_return_pct:+.2f}% (레버리지 반영 {leveraged_return_pct:+.2f}%) | 수익금 {profit_usdt:+.2f} USDT"
        )

    def show_trade_history(self):
        """매매 기록을 별도 창에 표로 보여준다."""
        win = tk.Toplevel(self.root)
        win.title("📊 자동매매 기록")
        win.geometry("900x500")

        summary_frame = tk.Frame(win, pady=8)
        summary_frame.pack(fill="x", padx=10)

        if self.trade_history:
            total = len(self.trade_history)
            wins = sum(1 for t in self.trade_history if t['leveraged_return_pct'] > 0)
            win_rate = wins / total * 100
            avg_return = sum(t['leveraged_return_pct'] for t in self.trade_history) / total
            total_profit = sum(t.get('profit_usdt', 0.0) for t in self.trade_history)
            summary_text = (
                f"총 거래 {total}건  |  승률 {win_rate:.1f}% ({wins}승 {total-wins}패)  |  "
                f"건당 평균 수익률 {avg_return:+.2f}%  |  총 수익금 {total_profit:+.2f} USDT"
            )
        else:
            summary_text = "아직 매매 기록이 없습니다."

        tk.Label(summary_frame, text=summary_text, font=("맑은 고딕", 10, "bold")).pack(side="left")
        tk.Button(summary_frame, text="기록 전체 삭제", command=lambda: self.clear_trade_history(win)).pack(side="right")

        columns = ("time", "side", "entry", "exit", "profit", "ret")
        tree = ttk.Treeview(win, columns=columns, show="headings", height=18)
        headers = {
            "time": "시각", "side": "방향", "entry": "진입가", "exit": "청산가",
            "profit": "수익금(USDT)", "ret": "수익률%"
        }
        widths = {
            "time": 160, "side": 60, "entry": 110, "exit": 110,
            "profit": 120, "ret": 100
        }
        for col in columns:
            tree.heading(col, text=headers[col])
            tree.column(col, width=widths[col], anchor="center")

        tree.tag_configure('win', foreground='#1a7a1a')
        tree.tag_configure('loss', foreground='#c0392b')

        for t in reversed(self.trade_history):  # 최신 기록이 위로
            tag = 'win' if t['leveraged_return_pct'] > 0 else 'loss'
            profit_usdt = t.get('profit_usdt', 0.0)  # 이 항목이 없는 옛 기록과도 호환
            tree.insert("", "end", values=(
                t['time'], t['side'].upper(), t['entry_price'], t['exit_price'],
                f"{profit_usdt:+.2f}", f"{t['leveraged_return_pct']:+.2f}"
            ), tags=(tag,))

        scrollbar = ttk.Scrollbar(win, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=(0, 10))
        scrollbar.pack(side="right", fill="y", pady=(0, 10))

    def clear_trade_history(self, win):
        if not messagebox.askyesno("확인", "저장된 매매 기록을 전부 삭제하시겠습니까? 되돌릴 수 없습니다."):
            return
        self.trade_history = []
        self.save_trade_history()
        win.destroy()
        messagebox.showinfo("완료", "매매 기록을 삭제했습니다.")

    # ------------------------- 거래소 선택 / API 발급 가이드 -------------------------
    def on_exchange_changed(self, event=None):
        self.load_saved_credentials()
        self.update_passphrase_visibility()
        self.btn_api_guide.config(text=f"📖 {self.var_exchange.get()} API Key 발급방법")

    def update_passphrase_visibility(self):
        exchange_name = self.var_exchange.get()
        needs_pp = EXCHANGE_OPTIONS.get(exchange_name, {}).get("needs_passphrase", False)
        if needs_pp:
            self.label_passphrase.grid()
            self.entry_passphrase.grid()
            self.label_passphrase_note.grid()
        else:
            self.label_passphrase.grid_remove()
            self.entry_passphrase.grid_remove()
            self.label_passphrase_note.grid_remove()

    def show_api_guide(self):
        exchange_name = self.var_exchange.get()
        guide_text = EXCHANGE_API_GUIDES.get(exchange_name, "안내를 찾을 수 없습니다.")

        win = tk.Toplevel(self.root)
        win.title(f"📖 {exchange_name} API Key 발급 방법")
        win.geometry("620x520")

        tk.Label(win, text=f"{exchange_name} API Key 발급 방법", font=("맑은 고딕", 13, "bold")).pack(
            anchor="w", padx=15, pady=(15, 5))

        text_frame = tk.Frame(win)
        text_frame.pack(fill="both", expand=True, padx=15, pady=(0, 15))
        sb = tk.Scrollbar(text_frame)
        sb.pack(side="right", fill="y")
        txt = tk.Text(text_frame, wrap="word", yscrollcommand=sb.set, font=("맑은 고딕", 10),
                      padx=10, pady=10)
        txt.insert("1.0", guide_text)
        txt.config(state="disabled")
        txt.pack(side="left", fill="both", expand=True)
        sb.config(command=txt.yview)

        tk.Button(win, text="닫기", command=win.destroy).pack(pady=(0, 10))

    # ------------------------- 주문 금액 방식 -------------------------
    def update_amount_mode_ui(self):
        if self.var_amount_mode.get() == "fixed":
            self.entry_amount.config(state="normal")
            self.entry_balance_pct.config(state="disabled")
        else:
            self.entry_amount.config(state="disabled")
            self.entry_balance_pct.config(state="normal")

    # ------------------------- 로그 -------------------------
    def log(self, message):
        self.root.after(0, self._log_on_main_thread, message)

    def _log_on_main_thread(self, message):
        self.log_text.config(state="normal")
        current_time = time.strftime("[%H:%M:%S] ")
        self.log_text.insert(tk.END, current_time + message + "\n")
        # 로그가 너무 쌓이면 UI가 느려지므로 500줄 초과 시 오래된 줄부터 제거
        line_count = int(self.log_text.index('end-1c').split('.')[0])
        if line_count > 500:
            self.log_text.delete('1.0', f'{line_count - 500}.0')
        self.log_text.see(tk.END)
        self.log_text.config(state="disabled")

    # ------------------------- 거래소 -------------------------
    def init_exchange(self):
        exchange_name = self.var_exchange.get()
        api_key = self.entry_api_key.get().strip()
        secret_key = self.entry_secret_key.get().strip()
        passphrase = self.entry_passphrase.get().strip()
        needs_passphrase = EXCHANGE_OPTIONS[exchange_name]["needs_passphrase"]
        leverage = int(self.spin_leverage.get())

        if not api_key or not secret_key:
            messagebox.showerror("오류", f"{exchange_name} API Key와 Secret Key를 입력해주세요.")
            return False

        if needs_passphrase and not passphrase:
            messagebox.showerror("오류", f"{exchange_name}는 API Key/Secret Key 외에 Passphrase도 필요합니다.\n입력해주세요.")
            return False

        try:
            exchange_class = _get_exchange_class(exchange_name)
            options = dict(EXCHANGE_OPTIONS[exchange_name]["options"])
            config = {
                'apiKey': api_key,
                'secret': secret_key,
                'enableRateLimit': True,
                'options': options
            }
            if needs_passphrase:
                config['password'] = passphrase  # ccxt 통합 필드명은 'password'로 passphrase를 받음

            self.exchange = exchange_class(config)
            self.exchange.load_markets()

            try:
                self.exchange.set_leverage(leverage, self.symbol)
                self.log(f"✅ 레버리지 {leverage}배 설정 완료")
            except Exception as le:
                self.log(f"ℹ️ 레버리지 자동 설정 건너뜀 ({exchange_name} 웹사이트 설정을 따릅니다): {le}")

            self.log(f"✅ 연동 성공: {exchange_name}")
            return True
        except Exception as e:
            messagebox.showerror("연동 실패", f"{exchange_name} 거래소 연결 실패:\n{e}")
            return False

    # ------------------------- 지표 -------------------------
    def compute_supertrend_engine(self, df, period, multiplier):
        high = df['high']
        low = df['low']
        close = df['close']

        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        atr = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

        hl2 = (high + low) / 2
        basic_ub = hl2 + multiplier * atr
        basic_lb = hl2 - multiplier * atr

        final_ub = basic_ub.copy()
        final_lb = basic_lb.copy()
        direction = pd.Series(1, index=df.index, dtype='int64')

        first_valid = atr.first_valid_index()
        if first_valid is None:
            return direction, final_ub, final_lb, atr

        start_pos = df.index.get_loc(first_valid)
        final_ub.iloc[start_pos] = basic_ub.iloc[start_pos]
        final_lb.iloc[start_pos] = basic_lb.iloc[start_pos]

        for i in range(start_pos + 1, len(df)):
            if basic_ub.iloc[i] < final_ub.iloc[i - 1] or close.iloc[i - 1] > final_ub.iloc[i - 1]:
                final_ub.iloc[i] = basic_ub.iloc[i]
            else:
                final_ub.iloc[i] = final_ub.iloc[i - 1]

            if basic_lb.iloc[i] > final_lb.iloc[i - 1] or close.iloc[i - 1] < final_lb.iloc[i - 1]:
                final_lb.iloc[i] = basic_lb.iloc[i]
            else:
                final_lb.iloc[i] = final_lb.iloc[i - 1]

            if direction.iloc[i - 1] == 1:
                direction.iloc[i] = -1 if close.iloc[i] < final_lb.iloc[i] else 1
            else:
                direction.iloc[i] = 1 if close.iloc[i] > final_ub.iloc[i] else -1

        return direction, final_ub, final_lb, atr

    def compute_atr_series(self, df, period):
        """Wilder's ATR을 외부 라이브러리 없이 직접 계산 (트레일링 스탑 전용, SuperTrend의 ATR과 기간이 다를 수 있음)"""
        high = df['high']
        low = df['low']
        close = df['close']

        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        atr = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        return atr

    def get_closed_candle_signals(self, timeframe, st_period, st_multiplier, atr_period):
        """완성된(마감된) 캔들 기준으로 SuperTrend 추세 신호와 트레일링 스탑용 ATR을 함께 계산해서 반환"""
        try:
            fetch_limit = max(150, st_period * 4, atr_period * 4 + 50)
            candles = self.exchange.fetch_ohlcv(self.symbol, timeframe=timeframe, limit=fetch_limit)
            df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

            min_needed = max(st_period, atr_period) + 3
            if len(df) < min_needed:
                self.log(f"⚠️ 캔들 데이터가 부족합니다 (받은 개수: {len(df)}, 필요: {min_needed}개 이상). 기간 설정을 낮추거나 잠시 후 재시도하세요.")
                return None, None, None

            direction, final_ub, final_lb, st_atr = self.compute_supertrend_engine(df, st_period, st_multiplier)
            trailing_atr_series = self.compute_atr_series(df, atr_period)

            idx = -2  # 마지막 "완성된" 봉
            closed_signal = direction.iloc[idx]
            closed_candle_ts = df['timestamp'].iloc[idx]

            trailing_atr = trailing_atr_series.iloc[idx]

            trailing_atr_value = float(trailing_atr) if pd.notna(trailing_atr) else None
            return float(closed_signal), closed_candle_ts, trailing_atr_value

        except Exception as e:
            self.log(f"❌ 지표 계산 에러: {e}")
            return None, None, None

    def get_current_position(self):
        try:
            positions = self.exchange.fetch_positions([self.symbol])
            for p in positions:
                contracts = float(p.get('contracts') or 0)
                if contracts > 0:
                    return {
                        'size': contracts,
                        'side': str(p.get('side', '')).lower(),
                        'entry_price': float(p.get('entryPrice') or 0),
                        'timestamp': p.get('timestamp')  # 포지션 생성/갱신 시각(ms) — 재시작 시 트레일링 기준가 복원용
                    }
        except Exception:
            pass
        return {'size': 0.0, 'side': 'none', 'entry_price': 0.0, 'timestamp': None}

    def reconstruct_trailing_extreme(self, position, timeframe):
        """프로그램이 재시작됐을 때, 거래소에 남아있는 포지션의 생성 시각 이후
        실제 캔들 데이터를 조회해서 '진입 후 진짜 최고가(롱)/최저가(숏)'를 복원한다.
        이렇게 해야 트레일링 스탑이 재시작 전 상태 그대로 이어진다.
        복원이 불가능하면(시각 정보 없음 등) None을 반환한다."""
        ts = position.get('timestamp')
        if not ts:
            return None
        try:
            candles = self.exchange.fetch_ohlcv(self.symbol, timeframe=timeframe, since=int(ts), limit=1000)
            if not candles:
                return None
            if position['side'] == 'long':
                highest = max(c[2] for c in candles)  # 고가(high) 컬럼
                return max(highest, position['entry_price'])
            else:
                lowest = min(c[3] for c in candles)  # 저가(low) 컬럼
                return min(lowest, position['entry_price'])
        except Exception as e:
            self.log(f"⚠️ 트레일링 기준가 복원 실패({e}) — 현재가 기준으로 새로 시작합니다.")
            return None

    def fetch_futures_balance(self):
        """선물 지갑의 실제 USDT 잔고를 조회한다. 잔고 비율(복리) 모드에서 사용."""
        try:
            bal = self.exchange.fetch_balance()
            usdt = bal.get('USDT', {})
            total = float(usdt.get('total') or 0)
            free = float(usdt.get('free') or 0)
            return total if total > 0 else free
        except Exception as e:
            self.log(f"⚠️ 잔고 조회 실패: {e}")
            return None

    def resolve_order_amount_usdt(self, amount_mode, fixed_amount_usdt, balance_pct, leverage):
        """모드에 따라 이번 진입에 사용할 USDT 주문 금액을 결정한다.
        잔고 비율 모드에서는 진입 시점마다 실제 잔고를 다시 조회해 계산하므로,
        수익이 쌓여 잔고가 늘어나면 다음 진입 금액도 자동으로 함께 커진다 (복리 효과)."""
        if amount_mode == "fixed":
            return fixed_amount_usdt, None

        balance = self.fetch_futures_balance()
        if balance is None or balance <= 0:
            self.log("⚠️ 잔고 조회 실패 또는 잔고 0 — 이번 주기 진입을 보류합니다.")
            return None, None

        # 레버리지 적용된 금액 = 잔고 × 레버리지 × 사용비율(%)
        order_amount = balance * leverage * (balance_pct / 100.0)
        return order_amount, balance

    def btc_amount_to_contracts(self, btc_amount):
        try:
            market = self.exchange.market(self.symbol)
            contract_size = float(market.get('contractSize') or 0.0001)
        except Exception:
            contract_size = 0.0001

        contracts = btc_amount / contract_size
        contracts = max(1, round(contracts))
        return contracts, contract_size

    def usdt_amount_to_contracts(self, usdt_amount, current_price):
        """USDT 주문 금액을 현재가 기준 BTC 수량으로 바꾼 뒤, 다시 거래소 계약 수로 환산한다."""
        btc_amount = usdt_amount / current_price
        contracts, contract_size = self.btc_amount_to_contracts(btc_amount)
        actual_btc = contracts * contract_size
        actual_usdt = actual_btc * current_price
        return contracts, contract_size, actual_btc, actual_usdt

    def execute_market_order(self, side, amount, reduce_only=False):
        try:
            try:
                amount = float(self.exchange.amount_to_precision(self.symbol, amount))
            except Exception:
                pass
            params = {'reduceOnly': True} if reduce_only else {}
            if side == 'buy':
                return self.exchange.create_market_buy_order(self.symbol, amount, params)
            elif side == 'sell':
                return self.exchange.create_market_sell_order(self.symbol, amount, params)
        except Exception as e:
            self.log(f"❌ 주문 실패 ({side}): {e}")
            return None

    # ------------------------- 제어 -------------------------
    def start_bot(self):
        amount_mode = self.var_amount_mode.get()
        if amount_mode == "fixed":
            try:
                amount_check = float(self.entry_amount.get().strip())
                if amount_check <= 0:
                    raise ValueError
            except Exception:
                messagebox.showerror("오류", "1회 주문 금액(USDT)을 올바른 숫자로 입력해주세요.")
                return
        else:
            try:
                pct_check = float(self.entry_balance_pct.get().strip())
                if not (0 < pct_check <= 100):
                    raise ValueError
            except Exception:
                messagebox.showerror("오류", "잔고 사용 비율(%)은 0보다 크고 100 이하인 숫자로 입력해주세요.")
                return

        if not self.init_exchange():
            return
        self.save_credentials()
        self.is_running = True
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.entry_amount.config(state="disabled")
        self.entry_balance_pct.config(state="disabled")
        self.radio_fixed.config(state="disabled")
        self.radio_balance_pct.config(state="disabled")
        self.spin_leverage.config(state="disabled")
        self.combo_exchange.config(state="disabled")
        self.trade_thread = threading.Thread(target=self.trading_loop, daemon=True)
        self.trade_thread.start()

    def stop_bot(self):
        self.is_running = False
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.radio_fixed.config(state="normal")
        self.radio_balance_pct.config(state="normal")
        self.spin_leverage.config(state="normal")
        self.combo_exchange.config(state="readonly")
        self.update_amount_mode_ui()  # 정지 후엔 선택된 모드에 맞는 칸만 다시 활성화

    # ------------------------- 메인 루프 -------------------------
    def trading_loop(self):
        timeframe = self.selected_tf

        poll_sec = max(3, FIXED_POLL_SEC)

        amount_mode = self.var_amount_mode.get()
        if amount_mode == "fixed":
            fixed_amount_usdt = float(self.entry_amount.get().strip())
            self.log(f"ℹ️ 주문 금액 방식: 고정 금액 {fixed_amount_usdt} USDT (진입 시점 실시간 가격 기준으로 BTC/계약수 자동 환산)")
            balance_pct = None
        else:
            fixed_amount_usdt = None
            balance_pct = float(self.entry_balance_pct.get().strip())
            try:
                leverage_preview = int(self.spin_leverage.get())
            except Exception:
                leverage_preview = 1
            self.log(f"ℹ️ 주문 금액 방식: 잔고 비율(복리) {balance_pct}% × 레버리지 {leverage_preview}배 "
                     f"— 매 진입마다 그 시점의 실제 잔고로 재계산됩니다 (수익이 쌓이면 주문 금액도 같이 늘어남)")

        st_period = FIXED_ST_PERIOD
        st_multiplier = FIXED_ST_MULTIPLIER

        atr_period = FIXED_ATR_PERIOD
        atr_multiplier = FIXED_ATR_MULTIPLIER

        # 레버리지는 매매 시작 시 한 번만 읽어 저장 (백그라운드 스레드에서 위젯 접근 방지)
        try:
            self._current_leverage = int(self.spin_leverage.get())
        except Exception:
            self._current_leverage = 1

        last_signal = None
        trailing_extreme = None  # 진입 후 최고가(롱) / 최저가(숏) — 트레일링 스탑 기준점

        while self.is_running:
            try:
                ticker = self.exchange.fetch_ticker(self.symbol)
                current_price = ticker['last']

                signal, candle_ts, atr_value = self.get_closed_candle_signals(
                    timeframe, st_period, st_multiplier, atr_period
                )
                position = self.get_current_position()

                if signal is None:
                    time.sleep(poll_sec)
                    continue

                if position['side'] == 'none':
                    trailing_extreme = None  # 포지션이 없으면 트레일링 기준점 초기화

                # 재시작 등으로 포지션은 있는데 트레일링 기준가가 아직 없는 경우 —
                # 거래소 캔들 데이터로 '진입 후 실제 최고가/최저가'를 복원 시도
                if position['side'] != 'none' and trailing_extreme is None:
                    restored = self.reconstruct_trailing_extreme(position, timeframe)
                    if restored is not None:
                        trailing_extreme = restored
                        self.log(f"♻️ 트레일링 기준가 복원 완료: {trailing_extreme:.2f} "
                                  f"(진입 이후 캔들 데이터 기준, {position['side'].upper()})")
                    else:
                        self.log("ℹ️ 트레일링 기준가 복원 불가 — 현재가부터 새로 추적합니다.")

                if position['side'] != 'none':
                    try:
                        contract_size = float(self.exchange.market(self.symbol).get('contractSize') or 0.0001)
                    except Exception:
                        contract_size = 0.0001
                    position_usdt = position['size'] * contract_size * current_price
                    pos_status = f"{position['side'].upper()}({position_usdt:.2f}USDT)"
                else:
                    pos_status = "없음"
                entry_price_status = f"{position['entry_price']:.2f}" if position['side'] != 'none' else "-"
                self.log(
                    f"현재가: {current_price} | 추세: "
                    f"{'🟢 LONG' if signal == 1.0 else '🔴 SHORT'} | 진입가: {entry_price_status} | 포지션: {pos_status}"
                )

                if last_signal is None:
                    last_signal = signal
                    self.log(
                        f"ℹ️ 초기 추세 확인: {'🟢 LONG' if signal == 1.0 else '🔴 SHORT'} "
                        f"(이 추세로는 진입하지 않습니다. 다음 추세 전환을 기다립니다)"
                    )
                    time.sleep(2)
                    for _ in range(poll_sec):
                        if not self.is_running:
                            break
                        time.sleep(1)
                    continue

                trend_changed = (signal != last_signal)
                atr_exit_done = False

                # ---- 1) 보유 중이면, ATR 트레일링 스탑 이탈 여부를 실시간 가격으로 먼저 확인해서 청산 ----
                if position['side'] != 'none' and atr_value is not None:
                    if position['side'] == 'long':
                        trailing_extreme = current_price if trailing_extreme is None else max(trailing_extreme, current_price)
                        stop_price = trailing_extreme - atr_value * atr_multiplier
                        if current_price <= stop_price:
                            self.log(f"🎯 [ATR 트레일링 청산] 롱 포지션 — 현재가 {current_price} ≤ 청산선 {stop_price:.2f} "
                                      f"(진입후 최고가 {trailing_extreme:.2f} - ATR×{atr_multiplier})")
                            order = self.execute_market_order('sell', position['size'], reduce_only=True)
                            if order is not None:
                                self.log("✅ 롱 ATR 트레일링 청산 완료 (다음 추세 전환까지 대기)")
                                self.record_trade('long', position['entry_price'], current_price, 'ATR트레일링청산', position['size'])
                                atr_exit_done = True
                                trailing_extreme = None
                                time.sleep(3)  # 거래소가 포지션 반영할 시간 확보 (2→3초)
                            else:
                                self.log("⚠️ 트레일링 청산 주문 실패 — 다음 주기에 재시도합니다.")

                    elif position['side'] == 'short':
                        trailing_extreme = current_price if trailing_extreme is None else min(trailing_extreme, current_price)
                        stop_price = trailing_extreme + atr_value * atr_multiplier
                        if current_price >= stop_price:
                            self.log(f"🎯 [ATR 트레일링 청산] 숏 포지션 — 현재가 {current_price} ≥ 청산선 {stop_price:.2f} "
                                      f"(진입후 최저가 {trailing_extreme:.2f} + ATR×{atr_multiplier})")
                            order = self.execute_market_order('buy', position['size'], reduce_only=True)
                            if order is not None:
                                self.log("✅ 숏 ATR 트레일링 청산 완료 (다음 추세 전환까지 대기)")
                                self.record_trade('short', position['entry_price'], current_price, 'ATR트레일링청산', position['size'])
                                atr_exit_done = True
                                trailing_extreme = None
                                time.sleep(3)  # 거래소가 포지션 반영할 시간 확보 (2→3초)
                            else:
                                self.log("⚠️ 트레일링 청산 주문 실패 — 다음 주기에 재시도합니다.")

                # ---- 2) 트레일링 스탑에 안 걸렸는데 추세가 먼저 반대로 바뀌면 → 그 시점에 스위칭(폴백) ----
                if not atr_exit_done and trend_changed:
                    self.log(
                        f"🔁 추세 전환 감지 (트레일링 스탑 도달 전): {'LONG' if last_signal == 1.0 else 'SHORT'} → "
                        f"{'LONG' if signal == 1.0 else 'SHORT'}"
                    )

                    if signal == 1.0 and position['side'] == 'short':
                        self.log("🔄 트레일링 미도달 상태에서 추세전환 — 기존 SHORT 청산 시도 (스위칭)")
                        order = self.execute_market_order('buy', position['size'], reduce_only=True)
                        if order is not None:
                            self.log("✅ SHORT 청산 확인")
                            self.record_trade('short', position['entry_price'], current_price, '추세전환청산', position['size'])
                            trailing_extreme = None
                            time.sleep(2)
                        else:
                            self.log("⚠️ SHORT 청산 실패 — 신규 LONG 진입을 보류합니다.")

                    elif signal == -1.0 and position['side'] == 'long':
                        self.log("🔄 트레일링 미도달 상태에서 추세전환 — 기존 LONG 청산 시도 (스위칭)")
                        order = self.execute_market_order('sell', position['size'], reduce_only=True)
                        if order is not None:
                            self.log("✅ LONG 청산 확인")
                            self.record_trade('long', position['entry_price'], current_price, '추세전환청산', position['size'])
                            trailing_extreme = None
                            time.sleep(2)
                        else:
                            self.log("⚠️ LONG 청산 실패 — 신규 SHORT 진입을 보류합니다.")

                # ---- 3) 청산 후(또는 원래 포지션이 없었고) 현재 추세 방향으로 신규 진입 ----
                #     단, "추세가 실제로 전환된 시점"에만 진입한다 (트레일링 청산만으로는 같은 방향 재진입하지 않음)
                if trend_changed:
                    position_now = self.get_current_position()
                    if position_now['side'] == 'none':
                        order_amount_usdt, fetched_balance = self.resolve_order_amount_usdt(
                            amount_mode, fixed_amount_usdt, balance_pct, self._current_leverage
                        )
                        if order_amount_usdt is None:
                            # 잔고 조회 실패 등 — 이번 신호는 건너뜀 (촘촘한 재시도 방지를 위해 대기)
                            last_signal = signal
                            time.sleep(poll_sec)
                            continue

                        if amount_mode == "balance_pct":
                            self.log(
                                f"ℹ️ 복리 계산: 잔고 {fetched_balance:.2f} USDT × 레버리지 {self._current_leverage}배 × "
                                f"{balance_pct}% = {order_amount_usdt:.2f} USDT"
                            )

                        entry_contracts, entry_contract_size, entry_actual_btc, entry_actual_usdt = \
                            self.usdt_amount_to_contracts(order_amount_usdt, current_price)
                        self.log(
                            f"ℹ️ 진입 수량 환산: {order_amount_usdt:.2f} USDT (현재가 {current_price}) → "
                            f"{entry_contracts}계약 ≈ {entry_actual_btc:.6f} BTC ≈ {entry_actual_usdt:.2f} USDT"
                        )
                        if signal == 1.0:
                            self.log("🚀 LONG 포지션 신규 진입")
                            self.execute_market_order('buy', entry_contracts)
                            trailing_extreme = current_price
                        elif signal == -1.0:
                            self.log("📉 SHORT 포지션 신규 진입")
                            self.execute_market_order('sell', entry_contracts)
                            trailing_extreme = current_price

                last_signal = signal

            except Exception as e:
                self.log(f"⚠️ 루프 예외 발생: {e}")

            for _ in range(poll_sec):
                if not self.is_running:
                    break
                time.sleep(1)

        self.log("■ 자동매매 프로그램이 정지되었습니다.")


if __name__ == "__main__":
    if not ensure_license_active():
        sys.exit(1)
    root = tk.Tk()
    app = GateioProSuperTrendBot(root)
    start_periodic_recheck(root)  # 프로그램 실행 중에도 주기적으로 라이선스 재검증
    root.mainloop()
