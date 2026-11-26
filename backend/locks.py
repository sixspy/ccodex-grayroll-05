"""行锁与争用痕（嵌进发布/熔断主写路径）。

grayroll-05 争用救绿。旧实现“先 SELECT 后 ON CONFLICT DO UPDATE”会让并发
败者在提交点后偷锁双成；只改 BEGIN IMMEDIATE 也不够：败者会在 SQLite 写锁
队列里阻塞到胜者 finally 删除锁行之后才读到空行。这里用两层封死：

1. 进程内按 key 的非阻塞互斥锁（本部署为单 uvicorn 进程 + 线程池，两路会话
   必在同一进程）：抢不到立即判争用，不排队等下一任，保证一成一败；
2. row_locks 主键唯一插入做持久占锁（兼作跨进程兜底与锁页观察），
   过期/同 owner 才可条件接管，绝不无条件 DO UPDATE 偷锁。

败方在任何业务写之前被拒，只在 contention_marks + audit_log 留争用核销痕，
正式 releases / flag_rules 零改。owner_nonce 每路请求唯一，同账号两路会话
也不互认重入。
"""
from __future__ import annotations

import sqlite3
import threading
import time
import uuid

from fastapi import APIRouter, Header, HTTPException

from audit_svc import write_contention
from auth import current_user
from db import connect

router = APIRouter(prefix="/api/locks", tags=["locks"])
TTL = 20.0

# --- 进程内互斥（同进程两路并发请求的决定性闸门） ---------------------------
_GUARD = threading.Lock()
_LOCAL_LOCKS: dict[str, threading.Lock] = {}
_LOCAL_HOLDERS: dict[str, tuple[str, str]] = {}


def new_owner() -> str:
    """每路写请求一个唯一归属，保证同账号两路会话也不重入同一把锁。"""
    return uuid.uuid4().hex


def _scoped_key(key: str) -> str:
    """把临时库路径纳入本地锁键，避免不同测试库之间串锁。"""
    try:
        import db

        return f"{db.DB_PATH}::{key}"
    except Exception:
        return key


def _local_try(scoped: str, holder: str, owner: str):
    """成功返回 None；已被他路持有时返回 (holder_name, owner)。"""
    with _GUARD:
        lk = _LOCAL_LOCKS.setdefault(scoped, threading.Lock())
    if lk.acquire(blocking=False):
        with _GUARD:
            _LOCAL_HOLDERS[scoped] = (holder, owner)
        return None
    with _GUARD:
        return _LOCAL_HOLDERS.get(scoped, ("unknown", ""))


def _local_release(scoped: str, owner: str) -> None:
    with _GUARD:
        current = _LOCAL_HOLDERS.get(scoped)
        lk = _LOCAL_LOCKS.get(scoped)
        if current and current[1] != owner:
            return
        _LOCAL_HOLDERS.pop(scoped, None)
    if lk is not None:
        try:
            lk.release()
        except RuntimeError:
            pass


def acquire(conn, key: str, holder: str, owner: str | None = None) -> None:
    owner = owner or f"name:{holder}"
    scoped = _scoped_key(key)

    winner = _local_try(scoped, holder, owner)
    if winner is not None:
        # 同进程已有一路持锁：败方只写争用核销痕，立即 409，不等待轮替。
        write_contention(conn, key, holder, winner[0])
        conn.commit()
        raise HTTPException(409, detail="发布锁争用，请稍后重试")

    now = time.time()
    try:
        # 持久层：主键直插，冲突即锁已存在；不做无条件 upsert 偷锁。
        conn.execute(
            "INSERT INTO row_locks(lock_key, holder, owner_nonce, expires_at) "
            "VALUES(?,?,?,?)",
            (key, holder, owner, now + TTL),
        )
        conn.commit()
        return
    except sqlite3.IntegrityError:
        pass

    row = conn.execute("SELECT * FROM row_locks WHERE lock_key=?", (key,)).fetchone()
    row_owner = row["owner_nonce"] if row is not None and "owner_nonce" in row.keys() else ""
    active_other = bool(
        row is not None
        and row["expires_at"] > now
        and row_owner != owner
    )
    if active_other:
        write_contention(conn, key, holder, row["holder"])
        conn.commit()
        _local_release(scoped, owner)
        raise HTTPException(409, detail="发布锁争用，请稍后重试")

    # 无行（刚被释放）/ 已过期 / 同 owner：条件接管。
    cur = conn.execute(
        "UPDATE row_locks SET holder=?, owner_nonce=?, expires_at=? "
        "WHERE lock_key=? AND (expires_at<=? OR owner_nonce=?)",
        (holder, owner, now + TTL, key, now, owner),
    )
    if cur.rowcount == 0:
        row = conn.execute("SELECT holder FROM row_locks WHERE lock_key=?", (key,)).fetchone()
        winner_name = row["holder"] if row is not None else "unknown"
        write_contention(conn, key, holder, winner_name)
        conn.commit()
        _local_release(scoped, owner)
        raise HTTPException(409, detail="发布锁争用，请稍后重试")
    conn.commit()


def release(conn, key: str, holder: str, owner: str | None = None) -> None:
    owner = owner or f"name:{holder}"
    try:
        conn.execute(
            "DELETE FROM row_locks WHERE lock_key=? AND owner_nonce=?", (key, owner)
        )
        conn.commit()
    except sqlite3.Error:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
    finally:
        _local_release(_scoped_key(key), owner)


@router.get("/contention")
def list_contention(authorization: str | None = Header(default=None), limit: int = 40):
    current_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM contention_marks ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return {"items": [dict(r) for r in rows]}
