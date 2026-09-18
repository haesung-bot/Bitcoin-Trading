# -*- coding: utf-8 -*-
"""이더리움 전용 — 본인용 빌드 (텔레그램 알림 포함).

본인용 BTC 빌드(hedged_martingale_bot_my.py)를 그대로 물려받고 종목만 ETH로 바꾼다.
매매 설정(25배 / 진입 3% / 최대 3차 / 손절 후 양방향 1시간)은 완전히 동일하다.

BTC 빌드와 같은 PC에서 함께 돌릴 수 있도록 파일을 전부 분리했다.
  · 설정(API 키·텔레그램) : ~/.eth_martingale_bot_config.json
  · 매매 상태             : ~/.eth_martingale_bot_state.json
  · 매매 기록             : ~/.eth_martingale_bot_trades.json

⚠️ 같은 거래소 계정에서 BTC 봇과 동시에 돌리면 잔고를 공유한다.
   두 봇이 각자 '잔고 전체'를 기준으로 진입 규모를 잡으므로 실제 노출이 두 배가 되고,
   교차 마진이라 증거금도 합쳐진다. 소액 테스트라도 계정(또는 서브계정)을 나누는 것이
   안전하다. 나눌 수 없으면 한쪽만 켜두시라.

빌드:
  python -m PyInstaller --onefile --noconsole --name "MYBOT_ETH" ^
      --collect-all ccxt --collect-all certifi hedged_martingale_bot_eth.py

  ※ hedged_martingale_bot.py, hedged_martingale_bot_gui.py,
     hedged_martingale_bot_my.py 와 같은 폴더에 두어야 한다.
"""

from __future__ import annotations

import os
import tkinter as tk

import hedged_martingale_bot as core
import hedged_martingale_bot_gui as dist
import hedged_martingale_bot_my as my

SYMBOL = "ETH/USDT:USDT"
COIN = "ETH"

# ── 종목 교체 ──────────────────────────────────────────────
# 브로커와 시세 조회 모두 EXCHANGE_SYMBOLS 를 먼저 보고, 없으면 SYMBOL 을 쓴다.
# 둘 다 바꿔 어느 경로로 들어와도 ETH가 되게 한다.
core.SYMBOL = SYMBOL
for _name in list(core.EXCHANGE_SYMBOLS):
    core.EXCHANGE_SYMBOLS[_name] = SYMBOL

# ── 파일 분리 ──────────────────────────────────────────────
# BTC 빌드와 같은 PC에서 돌려도 상태가 섞이지 않게 경로를 전부 따로 쓴다.
_HOME = os.path.expanduser("~")
core.STATE_PATH = os.path.join(_HOME, ".eth_martingale_bot_state.json")
core.TRADE_LOG_PATH = os.path.join(_HOME, ".eth_martingale_bot_trades.json")
dist.CONFIG_PATH = os.path.join(_HOME, ".eth_martingale_bot_config.json")


class EthBotGUI(my.MyBotGUI):
    """본인용 빌드와 동일하되 종목이 ETH인 화면."""

    def _build_widgets(self) -> None:
        super()._build_widgets()

        # 어떤 종목을 돌리는지 헷갈리지 않도록 맨 위에 크게 붙인다.
        # BTC 빌드와 나란히 띄워놓고 쓰다가 잘못 누르는 일을 막는 것이 목적이다.
        banner = tk.Frame(self.root, bg="#16a085")
        banner.pack(fill="x", side="top", before=self.root.pack_slaves()[0])
        tk.Label(banner, text=f"◆ {COIN} (이더리움) 전용 ◆   {SYMBOL}",
                 bg="#16a085", fg="white", font=("맑은 고딕", 12, "bold"),
                 pady=6).pack()
        tk.Label(banner, text="BTC 봇과 같은 계정에서 동시에 켜면 잔고를 공유합니다. "
                              "계정을 나누거나 한쪽만 켜주세요.",
                 bg="#16a085", fg="#ecf9f6", font=("맑은 고딕", 8)).pack(pady=(0, 5))

        self._install_entry_bindings(self.root)

    def __init__(self, root: tk.Tk):
        super().__init__(root)
        self.root.title(f"{COIN} 선물 자동매매 (내 계정용)")
        self._log(f"ℹ️ 종목: {SYMBOL}")
        self._log(f"ℹ️ 상태파일: {core.STATE_PATH}")


def main() -> None:
    root = tk.Tk()
    root.withdraw()
    if not dist.check_core_compatible(root):
        root.destroy()
        return
    root.deiconify()
    EthBotGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
