#!/usr/bin/env python3
"""release_ticket_regex_05 — grayroll-05 争用侧两路并发双请求脚本。

打同一个开关发布写口 POST /api/releases/publish（同 flag/env => 同一把
flag-release 锁），两路独立会话（admin / writer）在 Barrier 后同时提交
含合规变更单号的说明，核销：
  1. 状态码恰为一路 200、一路 409（禁止无锁双成）；
  2. 该 flag/env 的正式 releases 前后差恰为 1（败方零正式改）；
  3. /api/locks/contention 新增一条同 lock_key 争用痕。

用法：
  python scripts/release_ticket_regex_05.py                 # 自起临时 uvicorn + 临时库
  python scripts/release_ticket_regex_05.py --base-url http://127.0.0.1:8020
  GRAYROLL_BASE_URL=http://127.0.0.1:11815 scripts/release_ticket_regex_05

退出码 0 表示争用核销通过，1 表示出现无锁双成/账目不符。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

# 与 backend/tickets.py 同口径；能导入时直接用后端单一事实源。
try:
    from tickets import CHANGE_TICKET_RE as _RE  # type: ignore

    TICKET_RE = _RE
except Exception:  # pragma: no cover - 独立拷贝兜底
    TICKET_RE = re.compile(
        r"(?:变更单号|变更编号|变更单|单号|change[_\s-]?ticket|ticket\s*no\.?|ticket|issue|jira)"
        r"\s*[:：#]?\s*(?=[A-Za-z0-9._-]{1,40}\d)(?P<label>[A-Za-z0-9][A-Za-z0-9._-]{1,40})"
        r"|(?P<std>[A-Z]{2,12}[-_][A-Z0-9]*\d{2,}(?:[-_]\d{2,})*)"
        r"|(?P<bare>[A-Z]{2,12}\d{3,})|(#\d{3,})",
        re.IGNORECASE,
    )

FLAG_ID = int(os.environ.get("GRAYROLL_FLAG_ID", "5"))   # notify.batch_push
ENV_ID = int(os.environ.get("GRAYROLL_ENV_ID", "1"))     # dev
LOCK_KEY = f"flag-release:{FLAG_ID}:{ENV_ID}"


def _request(base_url: str, method: str, path: str, token: str = "", body: dict | None = None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(base_url.rstrip("/") + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8") or "{}")
        except Exception:
            payload = {}
        return e.code, payload


def login(base_url: str, username: str, password: str) -> str:
    status, body = _request(base_url, "POST", "/api/login",
                            body={"username": username, "password": password})
    assert status == 200, f"登录 {username} 失败: {status} {body}"
    return body["token"]


def official_count(base_url: str, token: str) -> int:
    status, body = _request(base_url, "GET", "/api/releases", token=token)
    assert status == 200, f"读取发布账失败: {status}"
    return sum(
        1 for r in body.get("official", [])
        if r.get("flag_id") == FLAG_ID and r.get("env_id") == ENV_ID
    )


def contention_count(base_url: str, token: str) -> int:
    status, body = _request(base_url, "GET", "/api/locks/contention?limit=100", token=token)
    assert status == 200, f"读取争用痕失败: {status}"
    return sum(1 for m in body.get("items", []) if m.get("lock_key") == LOCK_KEY)


def fire_concurrent_pair(base_url: str, ticket: str) -> list[tuple[int, dict]]:
    admin = login(base_url, "admin", "admin123")
    writer = login(base_url, "writer", "writer123")
    barrier = threading.Barrier(2)
    results: list[tuple[int, dict]] = []
    lock = threading.Lock()
    stamp = int(time.time() * 1000)
    payload = {
        "flag_id": FLAG_ID,
        "env_id": ENV_ID,
        "kind": "percent",
        "percent": 9,
        "summary": f"{ticket} release_ticket_regex_05 争用双请求 {stamp}",
        "use_shadow": False,
    }

    def worker(token: str):
        barrier.wait()
        status, body = _request(base_url, "POST", "/api/releases/publish", token=token, body=payload)
        with lock:
            results.append((status, body))

    t1 = threading.Thread(target=worker, args=(admin,))
    t2 = threading.Thread(target=worker, args=(writer,))
    t1.start(); t2.start(); t1.join(timeout=40); t2.join(timeout=40)
    return results


def wait_health(base_url: str, proc: subprocess.Popen | None) -> None:
    deadline = time.time() + 30
    last = None
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            raise RuntimeError(f"自建 uvicorn 提前退出 code={proc.returncode}")
        try:
            with urllib.request.urlopen(base_url + "/api/health", timeout=2) as r:
                if r.status == 200:
                    return
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.4)
    raise RuntimeError(f"服务未就绪: {base_url} last={last}")


def spawn_server() -> tuple[str, subprocess.Popen, str]:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    tmp_dir = tempfile.mkdtemp(prefix="grayroll05_")
    db_file = str(Path(tmp_dir) / "script.db")
    env = dict(os.environ)
    env["GRAYROLL_DB"] = db_file
    env["PYTHONPATH"] = str(BACKEND) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app:app", "--app-dir", str(BACKEND),
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        wait_health(base_url, proc)
    except Exception:
        proc.terminate()
        out = proc.stdout.read() if proc.stdout else ""
        print(out[-2000:])
        raise
    return base_url, proc, db_file


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=os.environ.get("GRAYROLL_BASE_URL", ""))
    ap.add_argument("--ticket", default="CHG-20261126-50")
    args = ap.parse_args()

    m = TICKET_RE.search(args.ticket)
    assert m, f"脚本内置单号未通过同口径正则: {args.ticket}"
    assert not TICKET_RE.search("notify.batch_push 无变更单号并发说明"), "负例不应命中单号正则"
    ticket = m.groupdict().get("std") or m.group(0)

    spawned = None
    base_url = args.base_url.rstrip("/")
    db_file = ""
    if not base_url:
        base_url, spawned, db_file = spawn_server()
        print(f"[spawn] 临时服务 {base_url} db={db_file}")
    try:
        admin = login(base_url, "admin", "admin123")
        before = official_count(base_url, admin)
        marks_before = contention_count(base_url, admin)

        results = fire_concurrent_pair(base_url, ticket)
        statuses = sorted(s for s, _ in results)
        time.sleep(0.2)
        after = official_count(base_url, admin)
        marks_after = contention_count(base_url, admin)
        delta = after - before
        mark_delta = marks_after - marks_before

        print(f"[pair] lock_key={LOCK_KEY} statuses={statuses}")
        for s, b in results:
            print(f"  -> {s} {b.get('detail') or b.get('mode') or ''}")
        print(f"[accounts] official releases {before} -> {after} (delta={delta}); "
              f"contention marks +{mark_delta}")

        ok = (
            statuses == [200, 409]
            and delta == 1
            and mark_delta >= 1
        )
        if ok:
            print("ZHENG-VERDICT: GREEN 一成一争用拒绝，败方零正式改，争用痕已核销")
            return 0
        print("ZHENG-VERDICT: RED/FAIL 未满足 [200,409] + 正式账恰增 1 + 争用痕")
        return 1
    finally:
        if spawned is not None:
            spawned.terminate()
            try:
                spawned.wait(timeout=8)
            except subprocess.TimeoutExpired:
                spawned.kill()


if __name__ == "__main__":
    raise SystemExit(main())
