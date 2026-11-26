"""审计与变更历史写入。"""
from __future__ import annotations

import time

from fastapi import APIRouter, Header

from auth import current_user
from db import connect

router = APIRouter(prefix="/api/audit", tags=["audit"])


def write_audit(conn, actor: str, action: str, entity: str, entity_id: int | None, detail: str) -> None:
    conn.execute(
        "INSERT INTO audit_log(actor, action, entity, entity_id, detail, created_at) VALUES(?,?,?,?,?,?)",
        (actor, action, entity, entity_id, detail, time.time()),
    )


def write_history(
    conn,
    actor: str,
    action: str,
    detail: str,
    flag_id: int | None = None,
    env_id: int | None = None,
    change_ticket: str = "",
    stage: str = "official",
) -> None:
    """变更历史同源写口。grayroll-05：双写 change_ticket，stage 区分正式/影子。"""
    conn.execute(
        "INSERT INTO change_history"
        "(flag_id, env_id, action, detail, actor, change_ticket, stage, created_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (flag_id, env_id, action, detail, actor, change_ticket or "", stage, time.time()),
    )


def write_contention(conn, lock_key: str, loser: str, winner: str) -> None:
    """争用核销痕：contention_marks 落争用账，audit_log 同步留审计痕（同一事务）。"""
    now = time.time()
    conn.execute(
        "INSERT INTO contention_marks(lock_key, loser, winner, created_at) VALUES(?,?,?,?)",
        (lock_key, loser, winner, now),
    )
    write_audit(
        conn,
        loser,
        "lock_contention",
        "row_lock",
        None,
        f"key={lock_key}; winner={winner}; loser={loser}",
    )


@router.get("")
def list_audit(authorization: str | None = Header(default=None), limit: int = 80):
    current_user(authorization)
    with connect() as conn:
        rows = conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return {"items": [dict(r) for r in rows]}
