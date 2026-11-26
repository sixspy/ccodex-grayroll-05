"""变更历史。

grayroll-05 救乙红：
- 支持 ?ticket= 按双写的 change_ticket 结构化检索（不再只能全量拉取）；
- 支持 ?scope=official|shadow 镜头：未升正影子只属于 shadow，不得在
  official（已发布）镜头里冒充齐套假绿；
- 每行回传 change_ticket / stage / official，供历史页同源渲染。
"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException

from auth import current_user
from db import connect

router = APIRouter(prefix="/api/history", tags=["history"])

SCOPES = {"all", "official", "shadow"}


@router.get("")
def list_history(
    authorization: str | None = Header(default=None),
    limit: int = 100,
    ticket: str | None = None,
    scope: str = "all",
    flag_id: int | None = None,
):
    current_user(authorization)
    if scope not in SCOPES:
        raise HTTPException(400, detail="scope 仅支持 all/official/shadow")
    where = []
    params: list[object] = []
    if ticket and ticket.strip():
        where.append("h.change_ticket LIKE ?")
        params.append(f"%{ticket.strip()}%")
    if scope == "official":
        where.append("h.stage='official'")
    elif scope == "shadow":
        where.append("h.stage='shadow'")
    if flag_id is not None:
        where.append("h.flag_id=?")
        params.append(flag_id)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    params.append(limit)
    with connect() as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                f"""
                SELECT h.*, f.key AS flag_key, e.code AS env_code
                FROM change_history h
                LEFT JOIN flags f ON f.id=h.flag_id
                LEFT JOIN environments e ON e.id=h.env_id
                {where_sql}
                ORDER BY h.id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        ]
    for row in rows:
        # 兼容迁移前未回填 stage 的极端旧行：含 shadow 动作一律视为影子痕。
        stage = row.get("stage") or "official"
        if stage == "official" and "shadow" in (row.get("action") or ""):
            stage = "shadow"
        row["stage"] = stage
        row["official"] = stage == "official"
        row.setdefault("change_ticket", "")
    return {"items": rows, "scope": scope, "ticket": ticket or ""}
