"""SQLite 상태 저장소.

자동화 파이프라인에서 저장소가 하는 일은 세 가지다.
  1. 멱등성 — 같은 상품을 두 번 등록하지 않는다 (uid + content_hash 로 판정)
  2. 재개    — 중간에 죽어도 마지막 성공 단계부터 다시 시작한다 (stage)
  3. 감사    — 어떤 페이로드를 보냈고 뭐가 돌아왔는지 전부 남긴다

SQLite 를 쓰는 이유는 운영 부담이 0이라서다. 상품 수가 수십만 건을 넘어가면
같은 스키마로 Postgres 에 옮기면 된다.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .models import (
    Listing, ListingResult, PricedProduct, PurchaseTask, RawProduct, Stage, to_dict,
)
from .utils.logging import get

log = get("storage")

# 단계 진행도. 값이 클수록 뒤 단계. FAILED 는 순위 밖(항상 기록).
STAGE_RANK = {
    Stage.CRAWLED.value: 0,
    Stage.PRICED.value: 1,
    Stage.IMAGED.value: 2,
    Stage.LISTED.value: 3,
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    uid            TEXT PRIMARY KEY,
    source         TEXT NOT NULL,
    source_id      TEXT NOT NULL,
    source_url     TEXT,
    name           TEXT,
    brand          TEXT,
    cost_price     INTEGER,
    content_hash   TEXT,
    stage          TEXT NOT NULL,
    raw_json       TEXT,
    priced_json    TEXT,
    media_json     TEXT,
    last_error     TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_products_stage ON products(stage);
CREATE INDEX IF NOT EXISTS idx_products_source ON products(source);

CREATE TABLE IF NOT EXISTS listings (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    uid                TEXT NOT NULL,
    market             TEXT NOT NULL,
    market_product_id  TEXT,
    ok                 INTEGER NOT NULL,
    dry_run            INTEGER NOT NULL,
    error              TEXT,
    request_json       TEXT,
    response_json      TEXT,
    created_at         TEXT NOT NULL,
    UNIQUE(uid, market, created_at)
);
CREATE INDEX IF NOT EXISTS idx_listings_uid ON listings(uid, market);

CREATE TABLE IF NOT EXISTS purchase_tasks (
    task_id        TEXT PRIMARY KEY,
    market         TEXT NOT NULL,
    order_id       TEXT NOT NULL,
    uid            TEXT,
    option_value   TEXT,
    quantity       INTEGER,
    buyer_paid     INTEGER,
    expected_cost  INTEGER,
    cost_drift     INTEGER DEFAULT 0,
    state          TEXT NOT NULL,
    receiver_json  TEXT,
    deeplink       TEXT,
    note           TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_state ON purchase_tasks(state);

CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    command     TEXT,
    started_at  TEXT,
    finished_at TEXT,
    stats_json  TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # ------------------------------------------------------- 멱등성
    def should_process(self, product: RawProduct, *, force: bool = False
                       ) -> tuple[bool, str]:
        """(처리해야 하는가, 사유)"""
        if force:
            return True, "force"
        row = self.conn.execute(
            "SELECT content_hash, stage FROM products WHERE uid=?", (product.uid,)
        ).fetchone()
        if row is None:
            return True, "신규"
        if row["content_hash"] != product.content_hash:
            return True, f"변경 감지({row['content_hash']} -> {product.content_hash})"
        if row["stage"] == Stage.FAILED.value:
            return True, "이전 실패 재시도"
        if row["stage"] != Stage.LISTED.value:
            return True, f"미완료 단계({row['stage']})부터 재개"
        return False, "이미 등록됨(변경 없음)"

    # ------------------------------------------------------- 저장
    def save_raw(self, product: RawProduct) -> None:
        with self.tx() as c:
            c.execute(
                """INSERT INTO products
                   (uid, source, source_id, source_url, name, brand, cost_price,
                    content_hash, stage, raw_json, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(uid) DO UPDATE SET
                     name=excluded.name, brand=excluded.brand,
                     cost_price=excluded.cost_price,
                     content_hash=excluded.content_hash,
                     stage=excluded.stage, raw_json=excluded.raw_json,
                     last_error=NULL, updated_at=excluded.updated_at""",
                (product.uid, product.source, product.source_product_id,
                 product.source_url, product.name, product.brand, product.cost_price,
                 product.content_hash, Stage.CRAWLED.value,
                 json.dumps(to_dict(product), ensure_ascii=False), _now(), _now()),
            )

    def save_priced(self, priced: PricedProduct) -> None:
        self._update(priced.raw.uid, Stage.PRICED,
                     priced_json=json.dumps(to_dict(priced), ensure_ascii=False))

    def save_media(self, uid: str, media) -> None:
        self._update(uid, Stage.IMAGED,
                     media_json=json.dumps(to_dict(media), ensure_ascii=False))

    def mark_failed(self, uid: str, error: str) -> None:
        self._update(uid, Stage.FAILED, last_error=error[:2000])

    def _update(self, uid: str, stage: Stage, **cols: Any) -> None:
        """stage 는 단조 증가시킨다.

        파이프라인은 이미지(3)를 마켓별 가격 계산(2)보다 먼저 돌리므로,
        그대로 덮어쓰면 진행 단계가 뒤로 간다. 진행도는 '가장 멀리 간 단계'여야
        재개 로직이 맞게 동작하므로 더 앞선 단계일 때만 갱신한다.
        FAILED 는 순위와 무관하게 항상 기록한다(사람이 봐야 할 상태라서).
        """
        current = self.conn.execute(
            "SELECT stage FROM products WHERE uid=?", (uid,)
        ).fetchone()
        next_stage = stage
        if stage is not Stage.FAILED and current:
            if STAGE_RANK.get(current["stage"], -1) > STAGE_RANK.get(stage.value, -1):
                next_stage = Stage(current["stage"])

        sets = ", ".join(f"{k}=?" for k in cols)
        sets = f"stage=?, updated_at=?{', ' + sets if sets else ''}"
        with self.tx() as c:
            c.execute(
                f"UPDATE products SET {sets} WHERE uid=?",
                (next_stage.value, _now(), *cols.values(), uid),
            )

    def save_listing(self, result: ListingResult) -> None:
        with self.tx() as c:
            c.execute(
                """INSERT OR REPLACE INTO listings
                   (uid, market, market_product_id, ok, dry_run, error,
                    request_json, response_json, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (result.product_uid, result.market, result.market_product_id,
                 int(result.ok), int(result.dry_run), result.error,
                 json.dumps(result.request_payload, ensure_ascii=False),
                 json.dumps(result.response, ensure_ascii=False), result.at),
            )
            if result.ok and not result.dry_run:
                c.execute(
                    "UPDATE products SET stage=?, updated_at=? WHERE uid=?",
                    (Stage.LISTED.value, _now(), result.product_uid),
                )

    # ------------------------------------------------------- 조회
    def get_priced(self, uid: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT priced_json FROM products WHERE uid=?", (uid,)
        ).fetchone()
        return json.loads(row["priced_json"]) if row and row["priced_json"] else None

    def expected_cost(self, uid: str) -> int:
        data = self.get_priced(uid)
        return int((data or {}).get("price", {}).get("cost_price", 0))

    def counts_by_stage(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT stage, COUNT(*) n FROM products GROUP BY stage"
        ).fetchall()
        return {r["stage"]: r["n"] for r in rows}

    def recent_listings(self, limit: int = 20) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM listings ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

    # ------------------------------------------------------- 주문
    def upsert_task(self, task: PurchaseTask) -> bool:
        """신규면 True. 이미 있으면 상태를 건드리지 않고 False."""
        existing = self.conn.execute(
            "SELECT 1 FROM purchase_tasks WHERE task_id=?", (task.task_id,)
        ).fetchone()
        if existing:
            return False
        with self.tx() as c:
            c.execute(
                """INSERT INTO purchase_tasks
                   (task_id, market, order_id, uid, option_value, quantity,
                    buyer_paid, expected_cost, cost_drift, state, receiver_json,
                    deeplink, note, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (task.task_id, task.market, task.market_order_id, task.product_uid,
                 task.option_value, task.quantity, task.buyer_paid, task.expected_cost,
                 task.cost_drift, task.state,
                 json.dumps(task.receiver, ensure_ascii=False),
                 task.deeplink, task.note, task.created_at, _now()),
            )
        return True

    def pending_tasks(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM purchase_tasks WHERE state='pending' ORDER BY created_at"
        ).fetchall()

    def get_task(self, task_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM purchase_tasks WHERE task_id=?", (task_id,)
        ).fetchone()

    def set_task_state(self, task_id: str, state: str, note: str = "") -> None:
        with self.tx() as c:
            c.execute(
                "UPDATE purchase_tasks SET state=?, note=?, updated_at=? WHERE task_id=?",
                (state, note, _now(), task_id),
            )

    # ------------------------------------------------------- 실행 기록
    def start_run(self, run_id: str, command: str) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO runs (run_id, command, started_at) VALUES (?,?,?)",
                (run_id, command, _now()),
            )

    def finish_run(self, run_id: str, stats: dict[str, Any]) -> None:
        with self.tx() as c:
            c.execute(
                "UPDATE runs SET finished_at=?, stats_json=? WHERE run_id=?",
                (_now(), json.dumps(stats, ensure_ascii=False), run_id),
            )
