"""grayroll-05 失败测乙（另一面的假绿：历史页把未升正影子当已发布齐套）。

红：
- /api/history 不支持按变更单号检索，行内也没有 change_ticket/stage 结构字段；
- 只写影子发布（未升正）后，历史口不分正式/影子，粗看“单号有记录、齐套”，
  实际正式 releases/flag_rules 零增，是为假绿。

绿：发布说明与历史检索双写跨表（releases.change_ticket 与
change_history.change_ticket），历史口支持 ticket + scope=official 检索，
未升正影子只能在 scope=shadow/all 中以 stage=shadow 出现，不得计入“已发布”。
"""
from __future__ import annotations

import db

FID = 5
EID = 1
T_OFF = "CHG-20261126-11"
T_SHADOW = "CHG-20261126-12"


def _h(client, user="admin", password="admin123"):
    r = client.post("/api/login", json={"username": user, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_yi_official_publish_ticket_searchable_cross_table(grayroll_db):
    client = grayroll_db
    h = _h(client)

    r = client.post(
        "/api/releases/publish",
        headers=h,
        json={
            "flag_id": FID,
            "env_id": EID,
            "kind": "percent",
            "percent": 11,
            "summary": f"{T_OFF} notify.batch_push 正式一成一灰度",
            "use_shadow": False,
        },
    )
    assert r.status_code == 200, r.text
    version = r.json()["version"]

    # 求值页已升版：在线求值读到的正式规则版本必须就是本次升版。
    ev = client.post(
        "/api/evaluate",
        headers=h,
        json={"flag_key": "notify.batch_push", "env_code": "dev", "subject_key": "u-0501"},
    )
    assert ev.status_code == 200, ev.text
    assert ev.json()["version"] == version
    # 五页同源：求值结果也应带同一变更单号。
    assert ev.json().get("change_ticket") == T_OFF

    # 历史页按单号检索：必须命中结构化双写单号，而不是全量返回。
    hr = client.get(f"/api/history?ticket={T_OFF}", headers=h)
    assert hr.status_code == 200, hr.text
    items = hr.json()["items"]
    assert items, "合规正式发布后，历史页必须能按变更单号检索到记录"
    assert all(row.get("change_ticket") == T_OFF for row in items), items
    publishes = [row for row in items if row["action"] == "publish"]
    assert publishes, "按单号检索结果中必须有正式 publish 动作"
    assert all(row.get("stage") == "official" for row in publishes)
    assert all(row.get("official") is True for row in publishes)

    # 双写跨表：releases 正式账与 change_history 都必须落单号。
    with db.connect() as conn:
        rel = conn.execute(
            "SELECT change_ticket FROM releases WHERE flag_id=? AND env_id=? "
            "ORDER BY id DESC LIMIT 1",
            (FID, EID),
        ).fetchone()
        assert rel is not None and rel["change_ticket"] == T_OFF
        hist = conn.execute(
            "SELECT DISTINCT change_ticket FROM change_history WHERE change_ticket=?",
            (T_OFF,),
        ).fetchall()
        assert hist, "change_history 必须双写变更单号"


def test_yi_unpromoted_shadow_must_not_look_published(grayroll_db):
    """假绿盯防：未升正影子不得在 official 历史镜头里冒充已发布齐套。"""
    client = grayroll_db
    h = _h(client)

    sr = client.post(
        "/api/releases/publish",
        headers=h,
        json={
            "flag_id": FID,
            "env_id": EID,
            "kind": "percent",
            "percent": 12,
            "summary": f"{T_SHADOW} notify.batch_push 仅影子草案",
            "use_shadow": True,
        },
    )
    assert sr.status_code == 200, sr.text
    sid = sr.json()["id"]

    # 关键反假绿断言：正式镜头按影子单号检索必须为空。
    official_view = client.get(
        f"/api/history?ticket={T_SHADOW}&scope=official", headers=h
    )
    assert official_view.status_code == 200, official_view.text
    assert official_view.json()["items"] == [], (
        "未升正影子不得出现在 scope=official（已发布）历史中，否则就是齐套假绿"
    )

    # 全量/影子镜头能看到它，但必须明确标注为 shadow / official=False。
    all_view = client.get(f"/api/history?ticket={T_SHADOW}", headers=h)
    items = all_view.json()["items"]
    assert items, "影子动作本身应在历史中留痕"
    shadow_rows = [row for row in items if row["action"] == "release_shadow"]
    assert shadow_rows, items
    assert all(row.get("stage") == "shadow" for row in shadow_rows)
    assert all(row.get("official") is False for row in shadow_rows)

    # 正式账确实没有该单号。
    with db.connect() as conn:
        n_rel = conn.execute(
            "SELECT COUNT(*) c FROM releases WHERE change_ticket=? AND official=1",
            (T_SHADOW,),
        ).fetchone()["c"]
        assert n_rel == 0

    # 升正之后，同一单号才允许进入 official 镜头。
    pr = client.post(f"/api/releases/shadow/{sid}/promote", headers=h, json={})
    assert pr.status_code == 200, pr.text
    after = client.get(f"/api/history?ticket={T_SHADOW}&scope=official", headers=h)
    actions = [row["action"] for row in after.json()["items"]]
    assert "release_promote" in actions, after.text
