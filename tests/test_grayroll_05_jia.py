"""grayroll-05 失败测甲（写口正式账实）。

红：用“无变更单号发布说明夹具”打 /api/releases/publish，当前实现仍返回 200，
    releases 正式行与 flag_rules 正式行双增（账实假绿）。
绿（命运取舍=甲路径，整笔拒写）：缺单号一律 4xx，releases / flag_rules /
    release_shadows 三类账目相对请求前零增。
"""
from __future__ import annotations

import json
from pathlib import Path

import db

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"

FID = 5  # notify.batch_push
EID = 1  # dev


def _admin_headers(client) -> dict:
    r = client.post("/api/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _formal_counts() -> dict:
    with db.connect() as conn:
        return {
            "releases_official": conn.execute(
                "SELECT COUNT(*) c FROM releases WHERE flag_id=? AND env_id=? AND official=1",
                (FID, EID),
            ).fetchone()["c"],
            "rules_official": conn.execute(
                "SELECT COUNT(*) c FROM flag_rules WHERE flag_id=? AND env_id=? AND official=1",
                (FID, EID),
            ).fetchone()["c"],
            "release_shadows": conn.execute(
                "SELECT COUNT(*) c FROM release_shadows WHERE flag_id=? AND env_id=?",
                (FID, EID),
            ).fetchone()["c"],
        }


def _missing_payload() -> dict:
    data = json.loads(
        (FIXTURES / "grayroll_05_note_missing_ticket.json").read_text(encoding="utf-8")
    )
    return {k: v for k, v in data.items() if not k.startswith("_")}


def _compliant_payload() -> dict:
    data = json.loads(
        (FIXTURES / "grayroll_05_note_with_ticket.json").read_text(encoding="utf-8")
    )
    return {k: v for k, v in data.items() if not k.startswith("_")}


def test_jia_missing_ticket_still_creates_official_release_and_rule(grayroll_db):
    """缺单号夹具复现：甲路径要求整笔拒写，正式发布与规则行零增。"""
    client = grayroll_db
    headers = _admin_headers(client)
    payload = _missing_payload()

    before = _formal_counts()
    resp = client.post("/api/releases/publish", headers=headers, json=payload)

    # 红在当前代码：200 + mode=official；拍板甲路径后必须 4xx 拒写。
    assert resp.status_code in (400, 422), (
        f"缺变更单号必须整笔拒写，实际 {resp.status_code}: {resp.text}"
    )
    after = _formal_counts()
    assert after == before, f"缺单号正式账必须零增，before={before} after={after}"


def test_jia_path_a_seals_shadow_side_door_too(grayroll_db):
    """甲路径不是乙路径：缺单号时连影子草案也不收（禁止两路混用/第三路折中）。"""
    client = grayroll_db
    headers = _admin_headers(client)
    payload = _missing_payload()
    payload["use_shadow"] = True

    before = _formal_counts()
    resp = client.post("/api/releases/publish", headers=headers, json=payload)
    assert resp.status_code in (400, 422), resp.text
    after = _formal_counts()
    assert after == before, f"甲路径整笔拒写，不得留影子侧门，before={before} after={after}"
    assert after["release_shadows"] == before["release_shadows"]


def test_jia_compliant_ticket_publish_writes_official_once(grayroll_db):
    """对照：含合规单号的说明不受闸门误伤，正式发布恰好落一笔。"""
    client = grayroll_db
    headers = _admin_headers(client)
    payload = _compliant_payload()

    before = _formal_counts()
    resp = client.post("/api/releases/publish", headers=headers, json=payload)
    assert resp.status_code == 200, resp.text
    assert resp.json()["mode"] == "official"
    after = _formal_counts()
    assert after["releases_official"] == before["releases_official"] + 1
    # 同 (flag,env) 正式规则唯一，替换式升版后计数不增但必须存在。
    assert after["rules_official"] >= 1
