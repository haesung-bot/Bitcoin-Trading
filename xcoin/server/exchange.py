"""
exchange.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
포인트 → Xcoin 전환.

전환은 "포인트를 태우고 코인을 보낸다"는 두 단계라서, 중간에 서버가 죽거나
전송이 실패하면 유저 자산이 사라질 수 있습니다. 그래서 아래처럼 처리합니다.

  1. 요청이 오면 포인트를 즉시 차감하고 conversions 에 기록을 남긴다 (한 트랜잭션)
     → 이 시점에 포인트는 '묶인' 상태. 두 번 쓰는 것이 불가능해진다.
  2. 상태를 pending → approved → sending → sent 로만 앞으로 굴린다.
  3. 전송이 최종 실패하거나 관리자가 거절하면 포인트를 그대로 돌려준다(refund).
     환불도 장부에 한 줄로 남고, 같은 건은 두 번 환불되지 않는다.

idempotency_key 는 앱이 만들어 보내는 "이 요청의 고유 이름"입니다.
네트워크가 끊겨 앱이 같은 요청을 다시 보내도 전환은 한 번만 일어납니다.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import sqlite3
import threading
import time

from . import chain, config, db, points, security
from .points import PointError

_worker_thread = None
_worker_stop = threading.Event()


# ===================== 견적 =====================

def quote(points_amount: int) -> dict:
    """전환하기 전에 얼마를 받게 되는지 미리 계산해서 보여준다."""
    points_amount = int(points_amount)
    fee = (points_amount * config.CONVERT_FEE_BPS) // 10000
    net = points_amount - fee
    return {
        "points": points_amount,
        "fee_points": fee,
        "net_points": net,
        "amount_wei": str(points.points_to_wei(net)),
        "amount": points.points_to_xcoin_str(net),
        "symbol": config.COIN_SYMBOL,
        "rate": f"{config.POINTS_PER_XCOIN} 포인트 = 1 {config.COIN_SYMBOL}",
        "min_points": config.MIN_CONVERT_POINTS,
    }


def _converted_today(conn: sqlite3.Connection, user_id: str) -> int:
    start = points.day_start_ts()
    row = conn.execute(
        "SELECT COALESCE(SUM(points_spent), 0) AS s FROM conversions "
        "WHERE user_id = ? AND created_at >= ? AND status != 'rejected'",
        (user_id, start),
    ).fetchone()
    return int(row["s"])


# ===================== 전환 요청 =====================

def request_conversion(user_id: str, points_amount: int,
                       idempotency_key: str = "") -> dict:
    points_amount = int(points_amount)
    if points_amount < config.MIN_CONVERT_POINTS:
        raise PointError(
            "below_minimum",
            f"최소 {config.MIN_CONVERT_POINTS} 포인트부터 전환할 수 있습니다.",
        )

    idempotency_key = (idempotency_key or "").strip() or security.new_nonce()
    now = int(time.time())

    with db.tx() as conn:
        points.ensure_user(conn, user_id)
        points.assert_not_blocked(conn, user_id)

        # 같은 요청이 이미 들어와 있으면 그대로 돌려준다.
        existing = conn.execute(
            "SELECT * FROM conversions WHERE idempotency_key = ?", (idempotency_key,)
        ).fetchone()
        if existing:
            return _row_to_dict(existing) | {"duplicate": True}

        wallet_row = conn.execute(
            "SELECT address FROM wallets WHERE user_id = ?", (user_id,)
        ).fetchone()
        if wallet_row is None:
            raise PointError("wallet_not_linked",
                             "먼저 지갑을 연동해야 전환할 수 있습니다.", 409)

        used = _converted_today(conn, user_id)
        if used + points_amount > config.DAILY_CONVERT_CAP_POINTS:
            raise PointError(
                "daily_convert_cap",
                f"하루 전환 한도({config.DAILY_CONVERT_CAP_POINTS} 포인트)를 초과합니다. "
                f"오늘 사용: {used}",
            )

        fee = (points_amount * config.CONVERT_FEE_BPS) // 10000
        net = points_amount - fee
        amount_wei = points.points_to_wei(net)
        if amount_wei <= 0:
            raise PointError("amount_too_small", "전환 수량이 너무 작습니다.")

        # 포인트 차감 (잔액 부족이면 여기서 PointError)
        points.apply_delta(
            conn, user_id, -points_amount, "convert",
            ref=f"convert:{idempotency_key}",
            memo=f"{net} points -> {config.COIN_SYMBOL}",
        )

        status = "approved" if config.AUTO_APPROVE_CONVERSION else "pending"
        cur = conn.execute(
            "INSERT INTO conversions(user_id, address, points_spent, fee_points, amount_wei, "
            "status, idempotency_key, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, wallet_row["address"], points_amount, fee, str(amount_wei),
             status, idempotency_key, now, now),
        )
        conv_id = cur.lastrowid
        row = conn.execute("SELECT * FROM conversions WHERE id = ?", (conv_id,)).fetchone()
        result = _row_to_dict(row)

    result["duplicate"] = False
    return result


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["amount"] = _wei_to_str(int(d["amount_wei"]))
    d["symbol"] = config.COIN_SYMBOL
    return d


def _wei_to_str(wei: int) -> str:
    whole, frac = divmod(wei, 10 ** config.COIN_DECIMALS)
    frac_str = str(frac).rjust(config.COIN_DECIMALS, "0").rstrip("0")
    return f"{whole}.{frac_str}" if frac_str else str(whole)


def get_conversion(conv_id: int) -> dict:
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM conversions WHERE id = ?", (int(conv_id),)).fetchone()
    if row is None:
        raise PointError("not_found", "전환 기록을 찾을 수 없습니다.", 404)
    return _row_to_dict(row)


def list_conversions(user_id: str, limit: int = 50) -> list:
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT * FROM conversions WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, int(limit)),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


# ===================== 승인 / 거절 / 환불 =====================

def approve(conv_id: int) -> dict:
    with db.tx() as conn:
        row = conn.execute("SELECT * FROM conversions WHERE id = ?", (int(conv_id),)).fetchone()
        if row is None:
            raise PointError("not_found", "전환 기록을 찾을 수 없습니다.", 404)
        if row["status"] != "pending":
            raise PointError("bad_status", f"승인할 수 없는 상태입니다: {row['status']}", 409)
        conn.execute(
            "UPDATE conversions SET status = 'approved', updated_at = ? WHERE id = ?",
            (int(time.time()), int(conv_id)),
        )
    return get_conversion(conv_id)


def reject(conv_id: int, reason: str = "관리자 거절") -> dict:
    """거절하면 차감했던 포인트를 그대로 돌려준다."""
    with db.tx() as conn:
        row = conn.execute("SELECT * FROM conversions WHERE id = ?", (int(conv_id),)).fetchone()
        if row is None:
            raise PointError("not_found", "전환 기록을 찾을 수 없습니다.", 404)
        if row["status"] not in ("pending", "approved"):
            raise PointError("bad_status", f"거절할 수 없는 상태입니다: {row['status']}", 409)
        _refund(conn, row, reason)
        conn.execute(
            "UPDATE conversions SET status = 'rejected', error = ?, updated_at = ? WHERE id = ?",
            (reason, int(time.time()), int(conv_id)),
        )
    return get_conversion(conv_id)


def _refund(conn: sqlite3.Connection, row: sqlite3.Row, reason: str) -> None:
    """
    포인트 환불. ref 가 conversion id 에 묶여 있어서
    같은 건에 대해 두 번 환불되지 않는다.
    """
    points.apply_delta(
        conn, row["user_id"], int(row["points_spent"]), "refund",
        ref=f"refund:{row['id']}",
        memo=reason,
    )


# ===================== 지급 워커 =====================

def process_pending_payouts(limit: int = None) -> dict:
    """
    approved 상태의 전환 건을 실제로 체인에 쏜다.
    워커 스레드가 주기적으로 부르지만, 관리자 API 로 수동 실행도 가능하다.
    """
    limit = limit or config.PAYOUT_BATCH_SIZE
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT id FROM conversions WHERE status = 'approved' ORDER BY id LIMIT ?",
        (int(limit),),
    ).fetchall()

    sent, failed = 0, 0
    for row in rows:
        conv_id = int(row["id"])
        # 'sending' 으로 선점. 워커가 두 개 떠 있어도 한 건이 두 번 나가지 않는다.
        with db.tx() as c:
            claimed = c.execute(
                "UPDATE conversions SET status = 'sending', updated_at = ? "
                "WHERE id = ? AND status = 'approved'",
                (int(time.time()), conv_id),
            )
            if claimed.rowcount == 0:
                continue
            conv = c.execute("SELECT * FROM conversions WHERE id = ?", (conv_id,)).fetchone()
            conv = dict(conv)

        try:
            tx_hash = chain.send_xcoin(
                conv["address"], int(conv["amount_wei"]), ref=conv["idempotency_key"]
            )
            with db.tx() as c:
                c.execute(
                    "UPDATE conversions SET status = 'sent', tx_hash = ?, error = '', "
                    "updated_at = ? WHERE id = ?",
                    (tx_hash, int(time.time()), conv_id),
                )
            sent += 1
        except Exception as exc:
            failed += 1
            _handle_payout_failure(conv_id, str(exc))

    return {"sent": sent, "failed": failed, "scanned": len(rows)}


def _handle_payout_failure(conv_id: int, error: str) -> None:
    """
    실패하면 재시도 횟수를 올리고 approved 로 되돌린다.
    한도를 넘기면 failed 로 확정하고 포인트를 환불한다.
    """
    with db.tx() as c:
        row = c.execute("SELECT * FROM conversions WHERE id = ?", (conv_id,)).fetchone()
        if row is None:
            return
        retry = int(row["retry_count"]) + 1
        if retry >= config.PAYOUT_MAX_RETRY:
            _refund(c, row, f"전송 실패로 자동 환불: {error[:200]}")
            c.execute(
                "UPDATE conversions SET status = 'failed', retry_count = ?, error = ?, "
                "updated_at = ? WHERE id = ?",
                (retry, error[:500], int(time.time()), conv_id),
            )
        else:
            c.execute(
                "UPDATE conversions SET status = 'approved', retry_count = ?, error = ?, "
                "updated_at = ? WHERE id = ?",
                (retry, error[:500], int(time.time()), conv_id),
            )


def recover_stuck_sending(older_than_seconds: int = 600) -> int:
    """
    서버가 전송 도중에 죽으면 'sending' 상태로 남는다.
    사람이 tx 를 확인한 뒤 다시 시도하도록 approved 로 되돌린다.
    (자동으로 되돌리면 이중 전송 위험이 있으므로 관리자 API 로만 부른다.)
    """
    cutoff = int(time.time()) - older_than_seconds
    with db.tx() as conn:
        cur = conn.execute(
            "UPDATE conversions SET status = 'approved', updated_at = ? "
            "WHERE status = 'sending' AND updated_at < ?",
            (int(time.time()), cutoff),
        )
        return cur.rowcount


def start_worker() -> None:
    """백그라운드 지급 워커를 띄운다."""
    global _worker_thread
    if _worker_thread is not None and _worker_thread.is_alive():
        return

    def loop():
        while not _worker_stop.is_set():
            try:
                process_pending_payouts()
            except Exception as exc:
                print(f"[xcoin] 지급 워커 오류: {exc}")
            _worker_stop.wait(config.PAYOUT_INTERVAL_SECONDS)

    _worker_thread = threading.Thread(target=loop, name="xcoin-payout", daemon=True)
    _worker_thread.start()


def stop_worker() -> None:
    _worker_stop.set()
