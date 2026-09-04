# -*- coding: utf-8 -*-
"""배포용 + 텔레그램 알림 (운영자 본인용).

배포용 화면(hedged_martingale_bot_gui.py)을 그대로 물려받고, 텔레그램 봇 토큰과
Chat ID 입력칸만 얹은 것이다. 매매 설정(레버리지·진입금액·최대 단계)과 로그 형식은
배포용과 완전히 동일하다.

배포용 파일을 고치지 않고 따로 둔 이유:
  · 배포용 EXE를 받는 사람에게까지 텔레그램 입력칸이 딸려가면 안 된다.
  · 배포용은 앞으로도 그대로 빌드해서 배포하고, 이 파일은 본인 것만 빌드한다.

빌드:
  python -m PyInstaller --onefile --noconsole --name "MYBOT" ^
      --collect-all ccxt --collect-all certifi hedged_martingale_bot_my.py

  ※ hedged_martingale_bot.py, hedged_martingale_bot_gui.py 와 같은 폴더에 두어야 한다.
"""

from __future__ import annotations

import json
import os
import tkinter as tk
from tkinter import messagebox

import requests

import hedged_martingale_bot as core
import hedged_martingale_bot_gui as dist

# 야간 정지 시간대 기본값(한국 시간). 21시에 들어가서 다음날 1시에 풀린다.
QUIET_START_DEFAULT = 21
QUIET_END_DEFAULT = 1
QUIET_TZ = 9                # 한국 표준시. 서버가 UTC여도 이 값으로 보정한다.


class MyBotGUI(dist.HedgedMartingaleGUI):
    """배포용 화면 + 텔레그램 알림 입력칸."""

    def __init__(self, root: tk.Tk):
        self._tg_token = ""     # 시작 버튼을 누른 순간의 값(매매 스레드에서 쓴다)
        self._tg_chat = ""
        super().__init__(root)
        self.root.title("비트코인 선물 자동매매 (내 계정용)")

    # ───────────── 화면 ─────────────
    def _build_widgets(self) -> None:
        super()._build_widgets()

        tg_frame = tk.LabelFrame(self.root, text=" 텔레그램 알림 ", padx=10, pady=10)
        # 배포용의 프레임 순서: [0] 거래소/API  [1] 매매 설정  [2] 제어 버튼 ...
        # 매매 설정 아래, 시작 버튼 위에 끼워 넣는다.
        children = self.root.winfo_children()
        if len(children) > 2:
            tg_frame.pack(fill="x", padx=15, pady=5, before=children[2])
        else:
            tg_frame.pack(fill="x", padx=15, pady=5)

        tk.Label(tg_frame, text="봇 토큰:").grid(row=0, column=0, sticky="w")
        self.tg_token_var = tk.StringVar()
        tk.Entry(tg_frame, textvariable=self.tg_token_var, width=52, show="*").grid(
            row=0, column=1, columnspan=2, sticky="w", pady=5)

        tk.Label(tg_frame, text="Chat ID:").grid(row=1, column=0, sticky="w")
        self.tg_chat_var = tk.StringVar()
        row = tk.Frame(tg_frame)
        row.grid(row=1, column=1, columnspan=2, sticky="w", pady=5)
        tk.Entry(row, textvariable=self.tg_chat_var, width=30).pack(side="left")
        tk.Button(row, text="🔍 대화방 ID 찾기", bg="#8e44ad", fg="white",
                  command=self._discover_chats).pack(side="left", padx=(8, 0))
        tk.Button(row, text="✈ 연결 테스트", bg="#2980b9", fg="white",
                  command=self._test_telegram).pack(side="left", padx=(6, 0))

        tk.Label(tg_frame,
                 text="※ 여러 곳에 동시 전송하려면 쉼표로 구분하세요 (예: 66721231, -1001234567890)."
                      "  그룹방 ID는 -100으로 시작합니다.",
                 fg="#7f8c8d", font=("맑은 고딕", 8)).grid(row=2, column=0, columnspan=3, sticky="w")
        tk.Label(tg_frame, text="※ 비워두면 텔레그램 없이 화면 로그로만 동작합니다.",
                 fg="#7f8c8d", font=("맑은 고딕", 8)).grid(row=3, column=0, columnspan=3, sticky="w")

        quiet_frame = tk.LabelFrame(self.root, text=" 야간 정지 시간대 ", padx=10, pady=10)
        if len(children) > 2:
            quiet_frame.pack(fill="x", padx=15, pady=5, before=children[2])
        else:
            quiet_frame.pack(fill="x", padx=15, pady=5)

        self.quiet_on_var = tk.BooleanVar(value=True)
        tk.Checkbutton(quiet_frame, variable=self.quiet_on_var,
                       text="이 시간에는 새 매매를 하지 않는다").grid(row=0, column=0, columnspan=6, sticky="w")

        tk.Label(quiet_frame, text="시작").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.quiet_start_var = tk.StringVar(value=str(QUIET_START_DEFAULT))
        tk.Spinbox(quiet_frame, from_=0, to=23, width=5, textvariable=self.quiet_start_var).grid(
            row=1, column=1, sticky="w", padx=(4, 0), pady=(4, 0))
        tk.Label(quiet_frame, text="시  ~  종료").grid(row=1, column=2, sticky="w", pady=(4, 0))
        self.quiet_end_var = tk.StringVar(value=str(QUIET_END_DEFAULT))
        tk.Spinbox(quiet_frame, from_=0, to=23, width=5, textvariable=self.quiet_end_var).grid(
            row=1, column=3, sticky="w", padx=(4, 0), pady=(4, 0))
        tk.Label(quiet_frame, text="시  (한국 시간)").grid(row=1, column=4, sticky="w", pady=(4, 0))

        tk.Label(quiet_frame,
                 text="※ 이 시간에는 신규 진입과 물타기를 하지 않습니다."
                      "  수익 구간이 오면 익절은 그대로 합니다.",
                 fg="#7f8c8d", font=("맑은 고딕", 8)).grid(row=2, column=0, columnspan=6, sticky="w", pady=(6, 0))
        tk.Label(quiet_frame,
                 text=f"※ 손절은 1차·2차에서는 걸지 않고 버티며, "
                      f"{dist.FIXED_MAX_STEPS}차에서는 이 시간에도 그대로 손절합니다.",
                 fg="#7f8c8d", font=("맑은 고딕", 8)).grid(row=3, column=0, columnspan=6, sticky="w")
        tk.Label(quiet_frame,
                 text="※ 1차·2차는 손절이 멈추므로 손실이 -2.5%보다 커질 수 있습니다.",
                 fg="#c0392b", font=("맑은 고딕", 8)).grid(row=4, column=0, columnspan=6, sticky="w")
        tk.Label(quiet_frame, text="※ 서버가 해외(UTC)에 있어도 한국 시간 기준으로 판단합니다.",
                 fg="#7f8c8d", font=("맑은 고딕", 8)).grid(row=5, column=0, columnspan=6, sticky="w")

        for var in (self.tg_token_var, self.tg_chat_var,
                    self.quiet_on_var, self.quiet_start_var, self.quiet_end_var):
            var.trace_add("write", self._schedule_save)

        self._install_entry_bindings(self.root)

    # ───────────── 입력칸 보조 ─────────────
    def _install_entry_bindings(self, widget) -> None:
        """모든 입력칸에 복사/붙여넣기와 오른쪽 클릭 메뉴를 직접 달아준다.

        한글 입력 상태(한/영 키가 '한')에서는 Ctrl+V의 keysym이 'v'로 오지 않아서,
        tkinter가 기본으로 걸어둔 붙여넣기 단축키가 아예 동작하지 않는다. Chat ID처럼
        긴 숫자를 복사해서 넣어야 하는 칸에서 이게 특히 문제가 된다.
        그래서 keysym 대신 keycode로도 판별해서 직접 처리한다.
        """
        for child in widget.winfo_children():
            if isinstance(child, tk.Entry):
                child.bind("<Control-KeyPress>", self._on_entry_ctrl_key)
                child.bind("<Button-3>", self._show_entry_menu)
            self._install_entry_bindings(child)

    @staticmethod
    def _on_entry_ctrl_key(event):
        # 윈도우 기준 keycode: A=65, C=67, V=86, X=88 (한/영 상태와 무관하게 같은 값)
        key = event.keysym.lower()
        code = event.keycode
        entry = event.widget
        try:
            if key == "v" or code == 86:
                try:
                    entry.delete("sel.first", "sel.last")
                except tk.TclError:
                    pass                      # 선택된 글자가 없으면 지울 것도 없다
                entry.insert("insert", entry.clipboard_get())
            elif key == "c" or code == 67:
                entry.clipboard_clear()
                entry.clipboard_append(entry.selection_get())
            elif key == "x" or code == 88:
                entry.clipboard_clear()
                entry.clipboard_append(entry.selection_get())
                entry.delete("sel.first", "sel.last")
            elif key == "a" or code == 65:
                entry.select_range(0, "end")
                entry.icursor("end")
            else:
                return None
        except tk.TclError:
            pass                              # 클립보드가 비었거나 선택이 없는 경우
        return "break"                        # 기본 동작까지 실행돼 두 번 붙는 것을 막는다

    def _show_entry_menu(self, event):
        entry = event.widget
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="붙여넣기",
                         command=lambda: self._entry_action(entry, "paste"))
        menu.add_command(label="복사", command=lambda: self._entry_action(entry, "copy"))
        menu.add_command(label="잘라내기", command=lambda: self._entry_action(entry, "cut"))
        menu.add_separator()
        menu.add_command(label="전체 선택", command=lambda: self._entry_action(entry, "all"))
        menu.add_command(label="지우기", command=lambda: entry.delete(0, "end"))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    @staticmethod
    def _entry_action(entry, what):
        try:
            if what == "paste":
                try:
                    entry.delete("sel.first", "sel.last")
                except tk.TclError:
                    pass
                entry.insert("insert", entry.clipboard_get())
            elif what == "copy":
                entry.clipboard_clear()
                entry.clipboard_append(entry.selection_get())
            elif what == "cut":
                entry.clipboard_clear()
                entry.clipboard_append(entry.selection_get())
                entry.delete("sel.first", "sel.last")
            elif what == "all":
                entry.select_range(0, "end")
                entry.icursor("end")
        except tk.TclError:
            pass

    def _fit_window_to_content(self) -> None:
        """내용에 맞추되, 화면 높이를 넘지 않게 한다.

        배포용 화면에 텔레그램 칸이 더해지면서 창이 978px까지 커졌다. 원래 코드는 그
        높이를 minsize로 못박아버려서, 화면이 그보다 작은 노트북에서는 아래쪽(시작 버튼,
        로그창)이 잘린 채 줄일 수도 없게 된다.
        """
        super()._fit_window_to_content()
        self.root.update_idletasks()
        want_w = self.root.winfo_reqwidth()
        want_h = self.root.winfo_reqheight()
        max_h = int(self.root.winfo_screenheight() * 0.88)   # 작업표시줄/제목표시줄 여유
        max_w = int(self.root.winfo_screenwidth() * 0.95)
        w, h = min(want_w, max_w), min(want_h, max_h)
        self.root.minsize(min(want_w, max_w), min(560, h))   # 세로는 얼마든지 줄일 수 있게
        self.root.geometry(f"{w}x{h}")

    # ───────────── 저장 / 복원 ─────────────
    # 배포용 설정 파일에 텔레그램 항목만 얹는다. 거래소별 API 키는 배포용 코드가 그대로 처리한다.
    def _save_credentials(self, exchange_name: str = None) -> None:
        super()._save_credentials(exchange_name)
        try:
            data = self._read_config()
            data["tg_token"] = self.tg_token_var.get().strip()
            data["tg_chat"] = self.tg_chat_var.get().strip()
            data["quiet_on"] = bool(self.quiet_on_var.get())
            data["quiet_start"] = self.quiet_start_var.get().strip()
            data["quiet_end"] = self.quiet_end_var.get().strip()
            with open(dist.CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception as e:
            self._log(f"텔레그램 설정 저장 실패: {e}")

    def _load_saved_credentials(self, exchange_name: str = None) -> None:
        super()._load_saved_credentials(exchange_name)
        try:
            data = self._read_config()
        except Exception:
            return
        # 거래소를 바꿀 때도 이 함수가 불리는데, 그때 텔레그램 값까지 되돌릴 필요는 없다.
        if exchange_name is None:
            self.tg_token_var.set(data.get("tg_token", ""))
            self.tg_chat_var.set(data.get("tg_chat", ""))
            if data.get("quiet_on") is not None:
                self.quiet_on_var.set(bool(data["quiet_on"]))
            if data.get("quiet_start"):
                self.quiet_start_var.set(str(data["quiet_start"]))
            if data.get("quiet_end"):
                self.quiet_end_var.set(str(data["quiet_end"]))

    # ───────────── 텔레그램 도우미 ─────────────
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
            messagebox.showerror("조회 실패",
                                 f"텔레그램이 거부했습니다.\n\n{desc}\n\n봇 토큰이 맞는지 확인하세요.")
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
                    data={"chat_id": chat, "text": "텔레그램 연결 테스트입니다. 이 메시지가 보이면 정상입니다."},
                    timeout=10,
                )
                body = resp.json()
            except Exception as e:
                fail_list.append((chat, f"연결 오류: {e}"))
                continue
            if body.get("ok"):
                ok_list.append(chat)
            else:
                fail_list.append((chat, body.get("description", "알 수 없는 오류")))

        for chat in ok_list:
            self._log(f"텔레그램 전송 성공: {chat}")
        for chat, why in fail_list:
            self._log(f"텔레그램 전송 실패: {chat} — {why}")

        if ok_list and not fail_list:
            messagebox.showinfo("연결 성공", f"{len(ok_list)}곳 모두 전송했습니다.\n텔레그램을 확인하세요.")
        elif ok_list:
            messagebox.showwarning(
                "일부 실패",
                f"성공 {len(ok_list)}곳 / 실패 {len(fail_list)}곳\n\n"
                + "\n".join(f"{c}: {w}" for c, w in fail_list)
                + "\n\n그룹방이면 봇을 초대했는지, ID가 -100으로 시작하는지 확인하세요.",
            )
        else:
            messagebox.showerror(
                "연결 실패",
                "\n".join(f"{c}: {w}" for c, w in fail_list)
                + "\n\n봇 토큰과 Chat ID를 다시 확인하세요.",
            )

    # ───────────── 시작 ─────────────
    def _on_start_clicked(self) -> None:
        # tkinter 변수는 매매 스레드에서 읽으면 안 되므로, 시작하는 순간 값을 복사해둔다.
        self._tg_token = self.tg_token_var.get().strip()
        self._tg_chat = self.tg_chat_var.get().strip()

        if self.quiet_on_var.get():
            try:
                start = int(float(self.quiet_start_var.get().strip()))
                end = int(float(self.quiet_end_var.get().strip()))
            except ValueError:
                messagebox.showerror("입력 오류", "야간 정지 시간은 0~23 사이의 숫자로 넣어주세요.")
                return
            if not (0 <= start <= 23 and 0 <= end <= 23):
                messagebox.showerror("입력 오류", "야간 정지 시간은 0~23 사이여야 합니다.")
                return
            if start == end:
                messagebox.showerror("입력 오류",
                                     "시작 시각과 종료 시각이 같습니다. 다르게 넣어주세요.")
                return
            core.QUIET_START_HOUR, core.QUIET_END_HOUR = start, end
            # 1차·2차는 버티고, 마지막 차수(3차)에서는 정지 시간대라도 손절한다.
            core.QUIET_STOP_LOSS_STEP = dist.FIXED_MAX_STEPS
        else:
            core.QUIET_START_HOUR = core.QUIET_END_HOUR = -1
            core.QUIET_STOP_LOSS_STEP = 0
        core.QUIET_TZ_OFFSET = QUIET_TZ

        super()._on_start_clicked()

    def _run_bot(self, exchange_name: str, api_key: str, api_secret: str, passphrase: str) -> None:
        core.TELEGRAM_BOT_TOKEN = self._tg_token
        core.TELEGRAM_CHAT_ID = self._tg_chat
        if core.quiet_hours_enabled():
            self._log(f"야간 정지 시간대 {core.quiet_hours_label()} (한국 시간) — "
                      f"신규 진입·물타기를 멈추고, 손절은 {core.QUIET_STOP_LOSS_STEP}차부터만 겁니다.")
        if self._tg_token and self._tg_chat:
            self._log(f"텔레그램 알림 켜짐 ({len(core.parse_chat_ids(self._tg_chat))}곳)")
        else:
            self._log("텔레그램 미설정 — 화면 로그로만 알립니다.")
        super()._run_bot(exchange_name, api_key, api_secret, passphrase)


def main() -> None:
    root = tk.Tk()
    root.withdraw()
    if not dist.check_core_compatible(root):
        root.destroy()
        return
    root.deiconify()
    MyBotGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
