"""
admin.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
운영자용 API. 모든 요청에 X-Admin-Secret 헤더가 필요합니다.

주로 쓰게 될 것들:
  POST /admin/games                   게임 등록 (secret 발급 — 이때 딱 한 번 보여줌)
  GET  /admin/stats                   전체 현황
  GET  /admin/conversions?status=     전환 요청 목록
  POST /admin/conversions/<id>/approve  수동 승인
  POST /admin/payouts/run             지급 즉시 실행
  GET  /admin/chain/status            재무 지갑 가스/토큰 잔고
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import time

from flask import Blueprint, jsonify, request

from . import chain, config, db, exchange, points, security
from .points import PointError

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.before_request
def _check_admin():
    if request.method == "OPTIONS":
        return None
    if not security.is_admin(request):
        return jsonify({"ok": False, "error": "forbidden",
                        "message": "관리자 인증에 실패했습니다."}), 403
    return None


def _body() -> dict:
    return request.get_json(silent=True) or {}


# ===================== 게임 =====================

@admin_bp.post("/games")
def create_game():
    """
    게임을 등록하고 secret 을 발급한다.
    ★ 응답의 secret 은 이때 한 번만 보여준다. 게임 백엔드 환경변수에 바로 저장할 것.
    """
    data = _body()
    game_id = str(data.get("game_id", "")).strip()
    name = str(data.get("name", "")).strip() or game_id
    if not game_id:
        raise PointError("bad_request", "game_id 가 필요합니다.")
    result = points.register_game(
        game_id=game_id,
        name=name,
        points_per_score=float(data.get("points_per_score", 0.1)),
        daily_point_cap=int(data.get("daily_point_cap", 2000)),
    )
    return jsonify({"ok": True, **result,
                    "warning": "secret 은 지금만 확인할 수 있습니다. 안전한 곳에 보관하세요."})


@admin_bp.get("/games")
def list_games():
    return jsonify({"ok": True, "items": points.list_games()})


@admin_bp.post("/games/<game_id>/update")
def update_game(game_id: str):
    data = _body()
    fields, values = [], []
    if "points_per_score" in data:
        fields.append("points_per_score = ?")
        values.append(float(data["points_per_score"]))
    if "daily_point_cap" in data:
        fields.append("daily_point_cap = ?")
        values.append(int(data["daily_point_cap"]))
    if "enabled" in data:
        fields.append("enabled = ?")
        values.append(1 if data["enabled"] else 0)
    if not fields:
        raise PointError("bad_request",
                         "바꿀 값이 없습니다. (points_per_score / daily_point_cap / enabled)")
    values.append(game_id)
    with db.tx() as conn:
        cur = conn.execute(f"UPDATE games SET {', '.join(fields)} WHERE game_id = ?", values)
        if cur.rowcount == 0:
            raise PointError("unknown_game", f"등록되지 않은 게임입니다: {game_id}", 404)
    return jsonify({"ok": True, "items": points.list_games()})


@admin_bp.post("/games/<game_id>/rotate-secret")
def rotate_secret(game_id: str):
    """secret 이 유출됐을 때 새로 발급한다. 게임 백엔드도 같이 바꿔야 한다."""
    new = security.new_secret()
    with db.tx() as conn:
        cur = conn.execute("UPDATE games SET secret = ? WHERE game_id = ?", (new, game_id))
        if cur.rowcount == 0:
            raise PointError("unknown_game", f"등록되지 않은 게임입니다: {game_id}", 404)
    return jsonify({"ok": True, "game_id": game_id, "secret": new})


# ===================== 현황 =====================

@admin_bp.get("/stats")
def stats():
    conn = db.get_conn()
    day_start = points.day_start_ts()

    def scalar(sql, args=()):
        row = conn.execute(sql, args).fetchone()
        return list(row)[0] if row else 0

    by_status = {
        r["status"]: {"count": r["c"], "points": r["p"]}
        for r in conn.execute(
            "SELECT status, COUNT(*) AS c, COALESCE(SUM(points_spent), 0) AS p "
            "FROM conversions GROUP BY status"
        ).fetchall()
    }
    sent_wei = sum(
        int(r["amount_wei"])
        for r in conn.execute(
            "SELECT amount_wei FROM conversions WHERE status = 'sent'"
        ).fetchall()
    )

    return jsonify({
        "ok": True,
        "users": scalar("SELECT COUNT(*) FROM users"),
        "wallets_linked": scalar("SELECT COUNT(*) FROM wallets"),
        "points_outstanding": scalar("SELECT COALESCE(SUM(points), 0) FROM balances"),
        "points_earned_total": scalar("SELECT COALESCE(SUM(lifetime_earned), 0) FROM balances"),
        "points_earned_today": scalar(
            "SELECT COALESCE(SUM(delta), 0) FROM point_events "
            "WHERE delta > 0 AND created_at >= ?", (day_start,)),
        "sessions_today": scalar(
            "SELECT COUNT(*) FROM play_sessions WHERE created_at >= ?", (day_start,)),
        "sessions_rejected_today": scalar(
            "SELECT COUNT(*) FROM play_sessions WHERE accepted = 0 AND created_at >= ?",
            (day_start,)),
        "conversions": by_status,
        "xcoin_sent_total": exchange._wei_to_str(sent_wei),
        "xcoin_sent_total_wei": str(sent_wei),
        "chain_mode": "simulation" if config.is_simulation() else "live",
    })


@admin_bp.get("/chain/status")
def chain_status():
    return jsonify({"ok": True, **chain.status()})


# ===================== 전환 관리 =====================

@admin_bp.get("/conversions")
def list_conversions():
    status_filter = request.args.get("status", "")
    limit = min(int(request.args.get("limit", 100)), 500)
    conn = db.get_conn()
    if status_filter:
        rows = conn.execute(
            "SELECT * FROM conversions WHERE status = ? ORDER BY id DESC LIMIT ?",
            (status_filter, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM conversions ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return jsonify({"ok": True, "items": [exchange._row_to_dict(r) for r in rows]})


@admin_bp.post("/conversions/<int:conv_id>/approve")
def approve_conversion(conv_id: int):
    return jsonify({"ok": True, **exchange.approve(conv_id)})


@admin_bp.post("/conversions/<int:conv_id>/reject")
def reject_conversion(conv_id: int):
    reason = str(_body().get("reason", "관리자 거절"))
    return jsonify({"ok": True, **exchange.reject(conv_id, reason)})


@admin_bp.post("/payouts/run")
def run_payouts():
    return jsonify({"ok": True, **exchange.process_pending_payouts()})


@admin_bp.post("/payouts/recover")
def recover_payouts():
    """
    'sending' 상태로 멈춘 건을 다시 시도 대기열로 되돌린다.
    ★ 되돌리기 전에 반드시 익스플로러에서 이미 전송된 tx 가 없는지 확인할 것 ★
    """
    older = int(_body().get("older_than_seconds", 600))
    count = exchange.recover_stuck_sending(older)
    return jsonify({"ok": True, "recovered": count})


# ===================== 유저 관리 =====================

@admin_bp.get("/users/<user_id>")
def user_detail(user_id: str):
    from . import wallet as wallet_mod
    return jsonify({
        "ok": True,
        "balance": points.get_balance(user_id),
        "wallet": wallet_mod.get_wallet(user_id),
        "history": points.history(user_id, 50),
        "conversions": exchange.list_conversions(user_id, 20),
    })


@admin_bp.post("/users/<user_id>/block")
def block_user(user_id: str):
    data = _body()
    blocked = 1 if data.get("blocked", True) else 0
    reason = str(data.get("reason", ""))
    with db.tx() as conn:
        points.ensure_user(conn, user_id)
        conn.execute(
            "UPDATE users SET blocked = ?, block_reason = ? WHERE user_id = ?",
            (blocked, reason, user_id),
        )
    return jsonify({"ok": True, "user_id": user_id, "blocked": bool(blocked), "reason": reason})


@admin_bp.post("/points/adjust")
def adjust_points():
    """
    운영자가 직접 포인트를 더하거나 뺀다. (이벤트 보상, 오지급 회수 등)
    ref 를 주면 같은 ref 로는 두 번 적용되지 않는다.
    """
    data = _body()
    user_id = str(data.get("user_id", ""))
    delta = int(data.get("delta", 0))
    memo = str(data.get("memo", "관리자 조정"))
    ref = str(data.get("ref", "")) or f"admin:{int(time.time() * 1000)}:{security.new_nonce()}"
    if not user_id or delta == 0:
        raise PointError("bad_request", "user_id 와 0이 아닌 delta 가 필요합니다.")
    with db.tx() as conn:
        new_points = points.apply_delta(conn, user_id, delta, "admin_adjust", ref, memo=memo)
    return jsonify({"ok": True, "user_id": user_id, "points": new_points, "ref": ref})


@admin_bp.get("/sessions")
def sessions():
    """치트 조사용. 거절된 시도만 보려면 rejected=1."""
    conn = db.get_conn()
    user_id = request.args.get("user_id", "")
    rejected_only = request.args.get("rejected", "") in ("1", "true")
    limit = min(int(request.args.get("limit", 100)), 500)

    sql = "SELECT * FROM play_sessions WHERE 1=1"
    args = []
    if user_id:
        sql += " AND user_id = ?"
        args.append(user_id)
    if rejected_only:
        sql += " AND accepted = 0"
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)

    rows = conn.execute(sql, args).fetchall()
    return jsonify({"ok": True, "items": [dict(r) for r in rows]})
