"""grayroll-05 争用失败测（两路会话抢同一开关发布锁）。

红：当前 acquire() 是“先 SELECT 后 upsert”的检查时序，两路并发会双 200，
    无 contention_marks 争用痕，正式发布账双增（无锁双成）。
绿：原子占锁后恰一路 200、败路 409 且只写争用痕；败方对正式 releases /
    flag_rules 零增。不同账号、同账号两路会话都必须互斥。
"""
from __future__ import annotations

import asyncio

import httpx
from httpx import ASGITransport

import db
from app import app

FID = 5
EID = 1
KEY = f"flag-release:{FID}:{EID}"


def _login(client, username: str, password: str) -> dict:
    r = client.post("/api/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _counts() -> dict:
    with db.connect() as conn:
        return {
            "releases": conn.execute(
                "SELECT COUNT(*) c FROM releases WHERE flag_id=? AND env_id=? AND official=1",
                (FID, EID),
            ).fetchone()["c"],
            "rules": conn.execute(
                "SELECT COUNT(*) c FROM flag_rules WHERE flag_id=? AND env_id=? AND official=1",
                (FID, EID),
            ).fetchone()["c"],
            "marks": conn.execute("SELECT COUNT(*) c FROM contention_marks").fetchone()["c"],
        }


async def _concurrent_publish(headers_a: dict, headers_b: dict, ticket: str) -> list[int]:
    payload = {
        "flag_id": FID,
        "env_id": EID,
        "kind": "percent",
        "percent": 9,
        "summary": f"{ticket} grayroll-05 两路并发争用同一开关发布锁",
        "use_shadow": False,
    }
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://grayroll-test") as ac:

        async def push(headers):
            r = await ac.post("/api/releases/publish", headers=headers, json=payload)
            return r.status_code

        return list(await asyncio.gather(push(headers_a), push(headers_b)))


def test_zheng_two_sessions_diff_users_one_wins_one_mark(grayroll_db):
    client = grayroll_db
    ha = _login(client, "admin", "admin123")
    hb = _login(client, "writer", "writer123")
    before = _counts()

    statuses = asyncio.run(_concurrent_publish(ha, hb, "CHG-20261126-21"))

    assert sorted(statuses) == [200, 409], f"必须一成一争用拒绝，实际 {statuses}"
    after = _counts()
    assert after["releases"] - before["releases"] == 1, statuses
    assert after["marks"] - before["marks"] == 1

    with db.connect() as conn:
        mark = conn.execute(
            "SELECT * FROM contention_marks WHERE lock_key=? ORDER BY id DESC LIMIT 1",
            (KEY,),
        ).fetchone()
    assert mark is not None
    assert mark["lock_key"] == KEY

    # 争用痕可经锁页/审计同源接口读到。
    cr = client.get("/api/locks/contention", headers=ha)
    assert cr.status_code == 200
    assert any(item["lock_key"] == KEY for item in cr.json()["items"])


def test_zheng_same_account_two_sessions_also_exclusive(grayroll_db):
    """两路会话即便同账号（不同 token）也不得重入同一把开关发布锁。"""
    client = grayroll_db
    h1 = _login(client, "admin", "admin123")
    h2 = _login(client, "admin", "admin123")
    before = _counts()

    statuses = asyncio.run(_concurrent_publish(h1, h2, "CHG-20261126-22"))

    assert sorted(statuses) == [200, 409], f"同账号两路会话也必须互斥，实际 {statuses}"
    after = _counts()
    assert after["releases"] - before["releases"] == 1
