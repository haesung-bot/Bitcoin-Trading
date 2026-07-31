"""
points.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
포인트 적립/차감의 핵심 로직.

게임에서 한 판이 끝나면 서버로 결과를 보내고, 서버는 아래 순서로 검사합니다.

  1. 게임이 등록되어 있고 활성화 상태인가
  2. 서명이 맞는가 (그 게임의 secret 을 아는 쪽이 보낸 게 맞는가)
  3. 타임스탬프가 최근인가 (오래된 요청 재전송 차단)
  4. nonce 가 처음 보는 값인가 (같은 판 두 번 제출 차단)
  5. 점수/플레이시간이 상식적인가 (치트 차단)
  6. 분당 제출 횟수가 과하지 않은가
  7. 일일 한도(게임별 + 유저 전체)에 걸리지 않는가

7번에서 한도를 넘으면 요청 전체를 거부하지 않고, 남은 한도만큼만 지급합니다.
(유저 입장에서 "왜 아무것도 안 들어오지?" 보다 덜 답답하고, 로그도 남습니다.)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import sqlite3
import time

from . import config, db, security


class PointError(Exception):
    """포인트 처리 중 유저에게 돌려줄 수 있는 오류."""

    def __init__(self, code: str, message: str, http_status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


# ===================== 시간 도우미 =====================

def day_start_ts(now: int = None) -> int:
    """설정된 시간대 기준으로 '오늘 0시'의 유닉스 시각."""
    now = now if now is not None else int(time.time())
    offset = config.DAY_UTC_OFFSET_HOURS * 3600
    return ((now + offset) // 86400) * 86400 - offset


# ===================== 유저 / 잔액 =====================

def ensure_user(conn: sqlite3.Connection, user_id: str) -> None:
    now = int(time.time())
    conn.execute(
        "INSERT OR IGNORE INTO users(user_id, created_at) VALUES(?, ?)",
        (user_id, now),
    )
    conn.execute(
        "INSERT OR IGNORE INTO balances(user_id, points, lifetime_earned, updated_at) "
        "VALUES(?, 0, 0, ?)",
        (user_id, now),
    )


def assert_not_blocked(conn: sqlite3.Connection, user_id: str) -> None:
    row = conn.execute(
        "SELECT blocked, block_reason FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    if row and row["blocked"]:
        raise PointError("user_blocked", f"차단된 계정입니다: {row['block_reason']}", 403)


def get_balance(user_id: str) -> dict:
    conn = db.get_conn()
    row = conn.execute(
        "SELECT points, lifetime_earned FROM balances WHERE user_id = ?", (user_id,)
    ).fetchone()
    points = row["points"] if row else 0
    lifetime = row["lifetime_earned"] if row else 0
    return {
        "user_id": user_id,
        "points": points,
        "lifetime_earned": lifetime,
        "convertible_xcoin": points_to_xcoin_str(points),
    }


def points_to_wei(points: int) -> int:
    """포인트를 Xcoin 최소 단위(wei)로. 정수 나눗셈 순서를 지켜 오차를 없앤다."""
    return (int(points) * (10 ** config.COIN_DECIMALS)) // config.POINTS_PER_XCOIN


def points_to_xcoin_str(points: int) -> str:
    """사람이 읽을 수 있는 Xcoin 수량 문자열."""
    wei = points_to_wei(points)
    whole, frac = divmod(wei, 10 ** config.COIN_DECIMALS)
    frac_str = str(frac).rjust(config.COIN_DECIMALS, "0").rstrip("0")
    return f"{whole}.{frac_str}" if frac_str else str(whole)


# ===================== 장부 기록 =====================

def apply_delta(conn: sqlite3.Connection, user_id: str, delta: int, reason: str,
                ref: str, game_id: str = "", memo: str = "") -> int:
    """
    장부에 한 줄 쓰고 잔액을 갱신한다. 반드시 db.tx() 안에서 호출할 것.
    같은 ref 가 이미 있으면 아무 일도 하지 않고 현재 잔액만 돌려준다(멱등).
    차감인데 잔액이 모자라면 PointError.
    """
    now = int(time.time())
    ensure_user(conn, user_id)

    try:
        conn.execute(
            "INSERT INTO point_events(user_id, game_id, delta, reason, ref, memo, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?)",
            (user_id, game_id, int(delta), reason, ref, memo, now),
        )
    except sqlite3.IntegrityError:
        # 이미 처리된 요청이다. 두 번 지급하지 않는다.
        row = conn.execute(
            "SELECT points FROM balances WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row["points"] if row else 0

    row = conn.execute(
        "SELECT points FROM balances WHERE user_id = ?", (user_id,)
    ).fetchone()
    current = row["points"] if row else 0
    new_points = current + int(delta)
    if new_points < 0:
        raise PointError("insufficient_points",
                         f"포인트가 부족합니다. (보유 {current}, 필요 {abs(int(delta))})")

    earned_add = int(delta) if int(delta) > 0 else 0
    conn.execute(
        "UPDATE balances SET points = ?, lifetime_earned = lifetime_earned + ?, updated_at = ? "
        "WHERE user_id = ?",
        (new_points, earned_add, now, user_id),
    )
    return new_points


def earned_today(conn: sqlite3.Connection, user_id: str, game_id: str = None) -> int:
    """오늘 적립된(양수) 포인트 합계. game_id 를 주면 그 게임만."""
    start = day_start_ts()
    if game_id:
        row = conn.execute(
            "SELECT COALESCE(SUM(delta), 0) AS s FROM point_events "
            "WHERE user_id = ? AND game_id = ? AND delta > 0 AND created_at >= ?",
            (user_id, game_id, start),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COALESCE(SUM(delta), 0) AS s FROM point_events "
            "WHERE user_id = ? AND delta > 0 AND created_at >= ?",
            (user_id, start),
        ).fetchone()
    return int(row["s"])


# ===================== 게임 등록 =====================

def register_game(game_id: str, name: str, points_per_score: float,
                  daily_point_cap: int) -> dict:
    now = int(time.time())
    secret = security.new_secret()
    with db.tx() as conn:
        existing = conn.execute(
            "SELECT game_id FROM games WHERE game_id = ?", (game_id,)
        ).fetchone()
        if existing:
            raise PointError("game_exists", f"이미 등록된 게임입니다: {game_id}", 409)
        conn.execute(
            "INSERT INTO games(game_id, name, secret, points_per_score, daily_point_cap, "
            "enabled, created_at) VALUES(?, ?, ?, ?, ?, 1, ?)",
            (game_id, name, secret, float(points_per_score), int(daily_point_cap), now),
        )
    return {
        "game_id": game_id,
        "name": name,
        "secret": secret,
        "points_per_score": points_per_score,
        "daily_point_cap": daily_point_cap,
    }


def get_game(conn: sqlite3.Connection, game_id: str) -> sqlite3.Row:
    return conn.execute("SELECT * FROM games WHERE game_id = ?", (game_id,)).fetchone()


def list_games() -> list:
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT game_id, name, points_per_score, daily_point_cap, enabled, created_at "
        "FROM games ORDER BY created_at"
    ).fetchall()
    return [dict(r) for r in rows]


# ===================== 핵심: 게임 결과 제출 =====================

def submit_play(game_id: str, user_id: str, score: int, duration_ms: int,
                nonce: str, timestamp: int, signature: str) -> dict:
    """
    게임 한 판의 결과를 받아 포인트를 지급한다.
    검사에 걸리면 PointError 를 던지고, play_sessions 에 거절 사유를 남긴다.
    """
    now = int(time.time())
    score = int(score)
    duration_ms = int(duration_ms)

    if score < 0 or duration_ms < 0:
        raise PointError("bad_request", "score/duration_ms 는 0 이상이어야 합니다.")
    if not nonce or len(nonce) < 8:
        raise PointError("bad_nonce", "nonce 는 8자 이상이어야 합니다.")

    conn = db.get_conn()
    game = get_game(conn, game_id)
    if game is None:
        raise PointError("unknown_game", f"등록되지 않은 게임입니다: {game_id}", 404)
    if not game["enabled"]:
        raise PointError("game_disabled", "현재 비활성화된 게임입니다.", 403)

    payload = security.canonical_payload(game_id, user_id, score, duration_ms, nonce, timestamp)
    if not security.verify_signature(game["secret"], payload, signature):
        _log_session(user_id, game_id, score, duration_ms, 0, nonce, False, "bad_signature")
        raise PointError("bad_signature", "서명이 올바르지 않습니다.", 401)
    if not security.timestamp_is_fresh(timestamp, now):
        _log_session(user_id, game_id, score, duration_ms, 0, nonce, False, "stale_timestamp")
        raise PointError("stale_timestamp", "요청 시각이 너무 오래되었습니다.", 401)

    # ---- 치트 판정 ----
    reason = _cheat_reason(conn, user_id, score, duration_ms)
    if reason:
        _log_session(user_id, game_id, score, duration_ms, 0, nonce, False, reason)
        raise PointError("rejected", f"비정상 플레이로 판단되어 거부되었습니다. ({reason})", 422)

    ref = f"play:{game_id}:{nonce}"

    with db.tx() as conn:
        assert_not_blocked(conn, user_id)
        ensure_user(conn, user_id)

        # 이미 처리된 세션인지 먼저 확인 (멱등 응답)
        dup = conn.execute("SELECT id FROM point_events WHERE ref = ?", (ref,)).fetchone()
        if dup:
            row = conn.execute(
                "SELECT points FROM balances WHERE user_id = ?", (user_id,)
            ).fetchone()
            return {
                "awarded": 0,
                "duplicate": True,
                "points": row["points"] if row else 0,
                "capped": False,
            }

        raw_award = int(score * float(game["points_per_score"]))

        # ---- 일일 한도 ----
        game_today = earned_today(conn, user_id, game_id)
        total_today = earned_today(conn, user_id)
        room_game = max(0, int(game["daily_point_cap"]) - game_today)
        room_total = max(0, config.DAILY_POINT_CAP_PER_USER - total_today)
        award = max(0, min(raw_award, room_game, room_total))
        capped = award < raw_award

        if award > 0:
            new_points = apply_delta(
                conn, user_id, award, "play", ref, game_id=game_id,
                memo=f"score={score}",
            )
        else:
            row = conn.execute(
                "SELECT points FROM balances WHERE user_id = ?", (user_id,)
            ).fetchone()
            new_points = row["points"] if row else 0
            # 한도로 0점이어도 nonce 는 소비시켜서 재제출을 막는다.
            conn.execute(
                "INSERT OR IGNORE INTO point_events"
                "(user_id, game_id, delta, reason, ref, memo, created_at) "
                "VALUES(?, ?, 0, 'play', ?, ?, ?)",
                (user_id, game_id, ref, f"daily_cap score={score}", now),
            )

        conn.execute(
            "INSERT INTO play_sessions(user_id, game_id, score, duration_ms, points_award, "
            "nonce, accepted, reject_reason, created_at) VALUES(?, ?, ?, ?, ?, ?, 1, '', ?)",
            (user_id, game_id, score, duration_ms, award, nonce, now),
        )

    return {
        "awarded": award,
        "duplicate": False,
        "points": new_points,
        "capped": capped,
        "daily_cap_remaining": max(0, config.DAILY_POINT_CAP_PER_USER - earned_today(db.get_conn(), user_id)),
    }


def _cheat_reason(conn: sqlite3.Connection, user_id: str, score: int, duration_ms: int) -> str:
    """치트로 보이면 사유 문자열, 정상이면 빈 문자열."""
    if score > config.MAX_SCORE_PER_SESSION:
        return "score_too_high"
    if duration_ms < config.MIN_SESSION_SECONDS * 1000:
        return "session_too_short"
    seconds = max(duration_ms / 1000.0, 0.001)
    if score / seconds > config.MAX_SCORE_PER_SECOND:
        return "score_rate_too_high"
    recent = conn.execute(
        "SELECT COUNT(*) AS c FROM play_sessions WHERE user_id = ? AND created_at >= ?",
        (user_id, int(time.time()) - 60),
    ).fetchone()
    if int(recent["c"]) >= config.MAX_SUBMITS_PER_MINUTE:
        return "too_many_submits"
    return ""


def _log_session(user_id: str, game_id: str, score: int, duration_ms: int,
                 award: int, nonce: str, accepted: bool, reject_reason: str) -> None:
    """거절된 시도도 남겨둔다. 나중에 누가 어떻게 뚫으려 했는지 보려면 필요하다."""
    try:
        with db.tx() as conn:
            conn.execute(
                "INSERT INTO play_sessions(user_id, game_id, score, duration_ms, points_award, "
                "nonce, accepted, reject_reason, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, game_id, score, duration_ms, award, nonce,
                 1 if accepted else 0, reject_reason, int(time.time())),
            )
    except Exception:
        pass  # 로깅 실패가 본 로직을 막으면 안 된다.


def history(user_id: str, limit: int = 50) -> list:
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT game_id, delta, reason, memo, created_at FROM point_events "
        "WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, int(limit)),
    ).fetchall()
    return [dict(r) for r in rows]
