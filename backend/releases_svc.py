"""发布生效：grayroll-05 拍板甲路径——缺变更单号整笔拒写，正式账零增。

口径封死（禁止与乙路径混用、禁止第三路折中）：
- POST /api/releases/publish 的说明必须含可识别变更单号；
- 缺单号时在拿锁/写库之前直接 400，releases 与 flag_rules 正式行零增，
  release_shadows 影子行同样不写（甲路径不开放“缺单号只落影子”的侧门）；
- 合规发布：单号双写 releases.change_ticket 与 change_history.change_ticket；
- 影子升正必须带单号出处，否则正式区零改。
并发：每路请求一个 owner nonce 占同一 flag-release 锁，败方 409 只记争用痕。
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from audit_svc import write_audit, write_history
from auth import current_user, require_writer
from db import connect
from locks import acquire, new_owner, release
from policies import assert_flag_publishable, assert_not_killed_or_allow_kill, describe_path
from rules_svc import replace_official_rule
from tickets import extract_change_ticket
from validators import require_int, require_percent, require_rule_kind, require_str

router = APIRouter(prefix="/api/releases", tags=["releases"])


class PublishIn(BaseModel):
    flag_id: int
    env_id: int
    kind: str
    percent: int = 0
    cohort_id: int | None = None
    summary: str
    use_shadow: bool = False


@router.get("")
def list_releases(authorization: str | None = Header(default=None)):
    current_user(authorization)
    with connect() as conn:
        official = [
            dict(r)
            for r in conn.execute(
                """
                SELECT rel.*, f.key AS flag_key, e.code AS env_code
                FROM releases rel
                JOIN flags f ON f.id=rel.flag_id
                JOIN environments e ON e.id=rel.env_id
                WHERE rel.official=1
                ORDER BY rel.id DESC
                """
            ).fetchall()
        ]
        shadows = [dict(r) for r in conn.execute("SELECT * FROM release_shadows ORDER BY id DESC").fetchall()]
    return {"official": official, "shadows": shadows}


@router.post("/publish")
def publish(body: PublishIn, authorization: str | None = Header(default=None)):
    """甲路径：单号合规后，正式发布同事务落库；use_shadow=true 走影子草案（同样须含单号）。"""
    user = current_user(authorization)
    require_writer(user)
    fid = require_int(body.flag_id, "开关", 1)
    eid = require_int(body.env_id, "环境", 1)
    kind = require_rule_kind(body.kind)
    pct = require_percent(body.percent)
    summary = require_str(body.summary, "发布说明", 4)
    ticket = extract_change_ticket(summary)
    if not ticket:
        # 命运取舍=甲路径：整笔拒写，不拿锁、不留影子、正式发布与规则行零增。
        raise HTTPException(
            400,
            detail=(
                "发布说明须含变更单号（如 CHG-20261126-05）；"
                "grayroll-05 甲路径已封死：缺单号整笔拒写，正式发布与规则零增"
            ),
        )
    key = f"flag-release:{fid}:{eid}"
    owner = new_owner()
    with connect() as conn:
        acquire(conn, key, user["username"], owner)
        try:
            fl = conn.execute("SELECT * FROM flags WHERE id=?", (fid,)).fetchone()
            if not fl:
                raise HTTPException(404, detail="开关不存在")
            assert_flag_publishable(fl["status"])
            assert_not_killed_or_allow_kill(fl["status"])
            if not conn.execute("SELECT 1 FROM environments WHERE id=?", (eid,)).fetchone():
                raise HTTPException(404, detail="环境不存在")
            if body.cohort_id and not conn.execute(
                "SELECT 1 FROM cohorts WHERE id=?", (body.cohort_id,)
            ).fetchone():
                raise HTTPException(404, detail="人群不存在")
            now = time.time()
            if body.use_shadow:
                cur = conn.execute(
                    "INSERT INTO release_shadows"
                    "(flag_id, env_id, summary, kind, percent, cohort_id, status, "
                    "change_ticket, created_at, created_by) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (fid, eid, summary, kind, pct, body.cohort_id, "open",
                     ticket, now, user["username"]),
                )
                sid = cur.lastrowid
                write_audit(conn, user["username"], "release_shadow", "release_shadow", sid, describe_path("release", True))
                write_history(
                    conn, user["username"], "release_shadow", summary,
                    flag_id=fid, env_id=eid, change_ticket=ticket, stage="shadow",
                )
                conn.commit()
                return {"mode": "shadow", "id": sid, "change_ticket": ticket,
                        "path": describe_path("release", True)}

            ver, _rule_id = replace_official_rule(
                conn, fid, eid, kind, pct, body.cohort_id, now, ticket
            )
            cur = conn.execute(
                "INSERT INTO releases(flag_id, env_id, version, summary, status, official, "
                "change_ticket, published_at, published_by) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (fid, eid, ver, summary, "published", 1, ticket, now, user["username"]),
            )
            rid = cur.lastrowid
            if fl["status"] in ("draft", "killed"):
                conn.execute("UPDATE flags SET status='active' WHERE id=?", (fid,))
            write_audit(conn, user["username"], "publish", "release", rid, describe_path("release", False))
            write_history(
                conn, user["username"], "publish", f"v{ver} {summary}",
                flag_id=fid, env_id=eid, change_ticket=ticket, stage="official",
            )
            conn.commit()
            return {"mode": "official", "id": rid, "version": ver,
                    "change_ticket": ticket, "path": describe_path("release", False)}
        except Exception:
            conn.rollback()
            raise
        finally:
            release(conn, key, user["username"], owner)
            conn.commit()


@router.post("/shadow/{shadow_id}/promote")
def promote_release_shadow(shadow_id: int, authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    require_writer(user)
    owner = new_owner()
    with connect() as conn:
        sh = conn.execute("SELECT * FROM release_shadows WHERE id=?", (shadow_id,)).fetchone()
        if not sh or sh["status"] != "open":
            raise HTTPException(400, detail="影子发布不可升正")
        fid, eid = sh["flag_id"], sh["env_id"]
        key = f"flag-release:{fid}:{eid}"
        acquire(conn, key, user["username"], owner)
        try:
            # 拿锁后重读，防止两路并发把同一 open 影子重复升正。
            sh = conn.execute("SELECT * FROM release_shadows WHERE id=?", (shadow_id,)).fetchone()
            if not sh or sh["status"] != "open":
                raise HTTPException(400, detail="影子发布不可升正或已被处理")
            ticket = sh["change_ticket"] if "change_ticket" in sh.keys() else ""
            ticket = extract_change_ticket(ticket or "") or extract_change_ticket(sh["summary"] or "")
            if not ticket:
                raise HTTPException(400, detail="影子发布缺少变更单号出处，不可升正，正式区零改")
            fl = conn.execute("SELECT * FROM flags WHERE id=?", (fid,)).fetchone()
            assert_flag_publishable(fl["status"])
            assert_not_killed_or_allow_kill(fl["status"])
            now = time.time()
            ver, _rule_id = replace_official_rule(
                conn, fid, eid, sh["kind"], sh["percent"], sh["cohort_id"], now, ticket
            )
            cur = conn.execute(
                "INSERT INTO releases(flag_id, env_id, version, summary, status, official, "
                "change_ticket, published_at, published_by) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (fid, eid, ver, sh["summary"], "published", 1, ticket, now, user["username"]),
            )
            rid = cur.lastrowid
            conn.execute("UPDATE release_shadows SET status='promoted' WHERE id=?", (shadow_id,))
            if fl["status"] in ("draft", "killed"):
                conn.execute("UPDATE flags SET status='active' WHERE id=?", (fid,))
            write_history(
                conn, user["username"], "release_promote", f"影子升正 v{ver} {ticket}",
                flag_id=fid, env_id=eid, change_ticket=ticket, stage="official",
            )
            write_audit(conn, user["username"], "release_promote", "release", rid, f"from_shadow={shadow_id}")
            conn.commit()
            return {"id": rid, "version": ver, "mode": "official", "change_ticket": ticket}
        except Exception:
            conn.rollback()
            raise
        finally:
            release(conn, key, user["username"], owner)
            conn.commit()
