# -*- coding: utf-8 -*-
"""최종 검증 — 공통 유틸."""
import sys, os, math, json, logging, tempfile

sys.path.insert(0, "/home/user/Bitcoin-Trading")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"  ✅ {name}" + (f"  — {detail}" if detail else ""))
    else:
        FAIL.append((name, detail))
        print(f"  ❌ {name}" + (f"  — {detail}" if detail else ""))
    return bool(cond)


def eq(name, got, want, tol=None):
    ok = (got == want) if tol is None else (abs(got - want) <= tol)
    return check(name, ok, f"got={got!r} want={want!r}")


def section(t):
    print("\n" + "─" * 76)
    print("■ " + t)
    print("─" * 76)


def report(title):
    print("\n" + "=" * 76)
    print(f"{title}: 통과 {len(PASS)} / 실패 {len(FAIL)}")
    if FAIL:
        print("\n실패 목록:")
        for n, d in FAIL:
            print(f"  ❌ {n}  {d}")
    print("=" * 76)
    return len(FAIL) == 0


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def time(self):
        return self.t

    def advance(self, s):
        self.t += s


class Exchange:
    """방향당 포지션이 하나뿐인 거래소. 봇 인스턴스를 새로 만들어도 유지된다."""

    def __init__(self, balance=1600.0, contract_size=0.0001, fee=0.0):
        self.balance = balance
        self.cs = contract_size
        self.fee = fee
        self.pos = {}
        self.orders = []
        self.fail_balance = False

    def get_balance(self):
        return 0.0 if self.fail_balance else self.balance

    def apply_pnl(self, p):
        self.balance += p

    def quantize_qty(self, qty, price):
        return math.floor(qty / self.cs + 1e-9) * self.cs

    def fill_order(self, side, is_entry, qty, price):
        k = getattr(side, "value", side)
        p = self.pos.setdefault(k, {"qty": 0.0, "cost": 0.0})
        self.orders.append((k, "진입" if is_entry else "청산", qty, price))
        self.balance -= qty * price * self.fee
        if is_entry:
            p["qty"] += qty
            p["cost"] += qty * price
        else:
            p["qty"] = max(0.0, p["qty"] - qty)
            if p["qty"] <= 1e-12:
                p["qty"] = 0.0
                p["cost"] = 0.0
            else:
                p["cost"] *= p["qty"] / (p["qty"] + qty)

    def fetch_position(self, side):
        k = getattr(side, "value", side)
        p = self.pos.get(k, {"qty": 0.0, "cost": 0.0})
        if p["qty"] <= 0:
            return {"qty": 0.0, "entry_price": None, "contract_size": self.cs}
        return {"qty": p["qty"], "entry_price": p["cost"] / p["qty"], "contract_size": self.cs}


class Notifier:
    def __init__(self):
        self.msgs = []
        self.tg = []

    def send(self, text, telegram_text=None):
        self.msgs.append(text)
        self.tg.append(text if telegram_text is None else telegram_text)


def fresh_path(name="state.json"):
    return os.path.join(tempfile.mkdtemp(), name)


def defaults(core, lev=20, pos=0.02):
    core.LEVERAGE, core.INITIAL_MARGIN_PCT = lev, pos
    core.STEP_TRIGGER_PCT, core.TP_PCT, core.STOP_LOSS_PCT = 0.012, 0.003, 0.025
    core.MAX_STEPS = 4
    core.MAX_CONSECUTIVE_SL = 3
    core.COOLDOWN_SEC = 180
    core.TREND_EMA_PERIOD = 0
    core.HEDGE_AT_STEP = 0
    core.SHOW_QTY_DETAIL = True
    core.TELEGRAM_SHOW_BALANCE = True
    core.MARGIN_MODE = "cross"
