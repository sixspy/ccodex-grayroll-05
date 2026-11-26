"""灰度规则只读与影子绑定（乙路径草案）。"""
from __future__ import annotations

import time

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from audit_svc import write_audit, write_history
from auth import current_user, require_writer
from db import connect
from locks import acquire, release
from policies import assert_flag_publishable, assert_not_killed_or_allow_kill, describe_path
from tickets import extract_change_ticket
from validators import require_int, require_percent, require_rule_kind

router = APIRouter(prefix="/api/rules", tags=["rules"])


def replace_official_rule(
    conn, fid: int, eid: int, kind: str, pct: int, cohort_id, now: float, change_ticket: str
):
    """规则服务正式写口（grayroll-05 甲路径）。

    任何正式 flag_rules 行的替换/新增都必须带可识别变更单号；缺单号直接 400，
    调用方事务随异常回滚，规则正式账零增。返回 (version, rule_id)。
    """
    ticket = extract_change_ticket(change_ticket or "")
    if not ticket:
        raise HTTPException(400, detail="正式规则写入缺少有效变更单号，规则正式账零增")
    old = conn.execute(
        "SELECT * FROM flag_rules WHERE flag_id=? AND env_id=? AND official=1", (fid, eid)
    ).fetchone()
    ver = (old["version"] + 1) if old else 1
    if old:
        conn.execute("DELETE FROM flag_rules WHERE id=?", (old["id"],))
    cur = conn.execute(
        "INSERT INTO flag_rules(flag_id, env_id, kind, percent, cohort_id, version, official, updated_at) "
        "VALUES(?,?,?,?,?,?,1,?)",
        (fid, eid, kind, pct, cohort_id, ver, now),
    )
    return ver, cur.lastrowid


class ShadowBindIn(BaseModel):
    flag_id: int
    env_id: int
    kind: str
    percent: int = 0
    cohort_id: int | None = None
    change_ticket: str | None = None


@router.get("")
def list_rules(authorization: str | None = Header(default=None)):
    current_user(authorization)
    with connect() as conn:
        official = [
            dict(r)
            for r in conn.execute(
                """
                SELECT r.*, f.key AS flag_key, e.code AS env_code
                FROM flag_rules r
                JOIN flags f ON f.id=r.flag_id
                JOIN environments e ON e.id=r.env_id
                WHERE r.official=1
                ORDER BY r.id
                """
            ).fetchall()
        ]
        shadows = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM rule_shadows ORDER BY id DESC"
            ).fetchall()
        ]
    return {"official": official, "shadows": shadows}


@router.post("/shadow-bind")
def shadow_bind(body: ShadowBindIn, authorization: str | None = Header(default=None)):
    """乙路径：只写影子人群/比例绑定，正式规则零改。"""
    user = current_user(authorization)
    require_writer(user)
    fid = require_int(body.flag_id, "开关", 1)
    eid = require_int(body.env_id, "环境", 1)
    kind = require_rule_kind(body.kind)
    pct = require_percent(body.percent)
    raw_ticket = (body.change_ticket or "").strip()
    ticket = extract_change_ticket(raw_ticket)
    if raw_ticket and not ticket:
        raise HTTPException(400, detail="变更单号格式无法识别，示例 CHG-20261126-05")
    key = f"flag-rule:{fid}:{eid}"
    with connect() as conn:
        acquire(conn, key, user["username"])
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
            cur = conn.execute(
                "INSERT INTO rule_shadows(flag_id, env_id, kind, percent, cohort_id, status, "
                "change_ticket, created_at, created_by) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (fid, eid, kind, pct, body.cohort_id, "open", ticket, now, user["username"]),
            )
            sid = cur.lastrowid
            write_audit(conn, user["username"], "rule_shadow", "rule_shadow", sid, describe_path("cohort_bind", True))
            write_history(
                conn, user["username"], "rule_shadow",
                f"影子绑定 flag={fid} env={eid}", flag_id=fid, env_id=eid,
                change_ticket=ticket, stage="shadow",
            )
            conn.commit()
            return {"mode": "shadow", "id": sid, "change_ticket": ticket,
                    "path": describe_path("cohort_bind", True)}
        finally:
            release(conn, key, user["username"])
            conn.commit()


@router.post("/shadow/{shadow_id}/promote")
def promote_rule_shadow(shadow_id: int, authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    require_writer(user)
    with connect() as conn:
        sh = conn.execute("SELECT * FROM rule_shadows WHERE id=?", (shadow_id,)).fetchone()
        if not sh or sh["status"] != "open":
            raise HTTPException(400, detail="影子绑定不可升正")
        fid, eid = sh["flag_id"], sh["env_id"]
        key = f"flag-rule:{fid}:{eid}"
        acquire(conn, key, user["username"])
        try:
            # 拿锁后重读，防止并发重复升正。
            sh = conn.execute("SELECT * FROM rule_shadows WHERE id=?", (shadow_id,)).fetchone()
            if not sh or sh["status"] != "open":
                raise HTTPException(400, detail="影子绑定不可升正或已被处理")
            ticket = sh["change_ticket"] if "change_ticket" in sh.keys() else ""
            ticket = extract_change_ticket(ticket or "")
            if not ticket:
                # 甲路径封死规则正式口：无单号出处的影子绑定不得升正为正式规则行。
                raise HTTPException(400, detail="规则影子缺少变更单号出处，不可升正，正式规则零改")
            now = time.time()
            ver, rid = replace_official_rule(
                conn, fid, eid, sh["kind"], sh["percent"], sh["cohort_id"], now, ticket
            )
            conn.execute("UPDATE rule_shadows SET status='promoted' WHERE id=?", (shadow_id,))
            if conn.execute("SELECT status FROM flags WHERE id=?", (fid,)).fetchone()["status"] == "draft":
                conn.execute("UPDATE flags SET status='active' WHERE id=?", (fid,))
            write_history(
                conn, user["username"], "rule_promote", f"影子规则升正 v{ver}",
                flag_id=fid, env_id=eid, change_ticket=ticket, stage="official",
            )
            write_audit(conn, user["username"], "rule_promote", "flag_rule", rid, f"from_shadow={shadow_id}")
            conn.commit()
            return {"id": rid, "version": ver, "mode": "official", "change_ticket": ticket}
        finally:
            release(conn, key, user["username"])
            conn.commit()
