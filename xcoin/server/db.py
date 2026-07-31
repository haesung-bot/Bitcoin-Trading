"""
db.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SQLite 스키마와 커넥션 관리.

설계 원칙
  1) point_events 는 "append-only 장부"입니다. 적립도 차감도 전부 한 줄씩 쌓이고,
     절대 수정/삭제하지 않습니다. 사고가 나도 장부를 되짚어 복구할 수 있습니다.
  2) balances 는 그 장부를 빠르게 읽기 위한 캐시(집계 결과)입니다.
     항상 point_events 와 같은 트랜잭션 안에서만 갱신합니다.
  3) 돈이 걸린 모든 테이블은 "중복 방지 키(unique)"를 가집니다.
     같은 요청이 두 번 들어와도 두 번 지급되지 않습니다.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os
import sqlite3
import threading
from contextlib import contextmanager

from . import config

_local = threading.local()

SCHEMA = """
PRAGMA journal_mode=WAL;

-- 게임 등록표. 게임마다 고유한 서버 비밀키(secret)를 가집니다.
CREATE TABLE IF NOT EXISTS games (
    game_id           TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    secret            TEXT NOT NULL,
    points_per_score  REAL NOT NULL DEFAULT 0.1,   -- 점수 1점당 몇 포인트
    daily_point_cap   INTEGER NOT NULL DEFAULT 2000, -- 이 게임 하나에서 하루 최대 포인트
    enabled           INTEGER NOT NULL DEFAULT 1,
    created_at        INTEGER NOT NULL
);

-- 유저. user_id 는 게임 쪽에서 쓰는 식별자를 그대로 씁니다.
CREATE TABLE IF NOT EXISTS users (
    user_id     TEXT PRIMARY KEY,
    created_at  INTEGER NOT NULL,
    blocked     INTEGER NOT NULL DEFAULT 0,
    block_reason TEXT NOT NULL DEFAULT ''
);

-- 포인트 장부 (append-only)
CREATE TABLE IF NOT EXISTS point_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    game_id     TEXT NOT NULL DEFAULT '',
    delta       INTEGER NOT NULL,          -- 적립은 +, 차감은 -
    reason      TEXT NOT NULL,             -- play / bonus / convert / admin_adjust / refund
    ref         TEXT NOT NULL,             -- 중복 방지 키 (게임 세션 nonce 등)
    memo        TEXT NOT NULL DEFAULT '',
    created_at  INTEGER NOT NULL,
    UNIQUE(ref)
);
CREATE INDEX IF NOT EXISTS idx_point_events_user_time
    ON point_events(user_id, created_at);

-- 잔액 캐시
CREATE TABLE IF NOT EXISTS balances (
    user_id        TEXT PRIMARY KEY,
    points         INTEGER NOT NULL DEFAULT 0,
    lifetime_earned INTEGER NOT NULL DEFAULT 0,
    updated_at     INTEGER NOT NULL
);

-- 지갑 연동
CREATE TABLE IF NOT EXISTS wallets (
    user_id     TEXT PRIMARY KEY,
    address     TEXT NOT NULL,
    chain_id    INTEGER NOT NULL,
    verified_at INTEGER NOT NULL,
    UNIQUE(address)
);

-- 지갑 소유 증명용 일회성 챌린지
CREATE TABLE IF NOT EXISTS wallet_nonces (
    user_id     TEXT PRIMARY KEY,
    nonce       TEXT NOT NULL,
    issued_at   INTEGER NOT NULL
);

-- 포인트 → Xcoin 전환 요청
CREATE TABLE IF NOT EXISTS conversions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          TEXT NOT NULL,
    address          TEXT NOT NULL,
    points_spent     INTEGER NOT NULL,
    fee_points       INTEGER NOT NULL DEFAULT 0,
    amount_wei       TEXT NOT NULL,       -- 큰 수라 문자열로 저장
    status           TEXT NOT NULL,       -- pending / approved / sending / sent / failed / rejected
    tx_hash          TEXT NOT NULL DEFAULT '',
    error            TEXT NOT NULL DEFAULT '',
    retry_count      INTEGER NOT NULL DEFAULT 0,
    idempotency_key  TEXT NOT NULL,
    created_at       INTEGER NOT NULL,
    updated_at       INTEGER NOT NULL,
    UNIQUE(idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_conversions_status ON conversions(status);
CREATE INDEX IF NOT EXISTS idx_conversions_user ON conversions(user_id, created_at);

-- 게임 세션 제출 기록 (치트 탐지 및 감사용)
CREATE TABLE IF NOT EXISTS play_sessions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      TEXT NOT NULL,
    game_id      TEXT NOT NULL,
    score        INTEGER NOT NULL,
    duration_ms  INTEGER NOT NULL,
    points_award INTEGER NOT NULL,
    nonce        TEXT NOT NULL,
    accepted     INTEGER NOT NULL,
    reject_reason TEXT NOT NULL DEFAULT '',
    created_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_play_sessions_user ON play_sessions(user_id, created_at);
"""


def _connect() -> sqlite3.Connection:
    directory = os.path.dirname(config.DB_PATH)
    if directory:
        os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def get_conn() -> sqlite3.Connection:
    """스레드마다 하나씩 커넥션을 재사용한다."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _connect()
        _local.conn = conn
    return conn


@contextmanager
def tx():
    """
    쓰기 트랜잭션. 포인트가 움직이는 모든 작업은 반드시 이 안에서 해야 한다.
    BEGIN IMMEDIATE 로 시작해서 동시에 두 요청이 같은 잔액을 건드리는 것을 막는다.
    """
    conn = get_conn()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def init_db() -> None:
    conn = get_conn()
    conn.executescript(SCHEMA)


def reset_for_tests(path: str) -> None:
    """테스트에서만 사용. DB 경로를 바꾸고 커넥션을 새로 연다."""
    config.DB_PATH = path
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
    _local.conn = None
    init_db()
