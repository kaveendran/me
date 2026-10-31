"""The approval queue: agents propose, autonomy.toml decides, the executor acts.

A proposal carries everything needed to act later without the model re-deciding:
an executor kind and its exact arguments. `auto` actions run at once (within the
agent's daily cap); `approve` actions wait for the user; `off` actions are dropped.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime

from . import claude, db
from .config import Config, autonomy_level

# Reversible, private housekeeping the UI and phone may batch-approve.
SAFE_ACTIONS = frozenset({"label_own_repo_issues", "mark_notifications_read", "focus_block", "break_buffer",
                          "file_capture", "prep_note", "diagnose_ci_failure"})
_GH_ALLOWED = {("issue", "edit"), ("issue", "comment"), ("pr", "comment"), ("pr", "review")}
_GH_API_ALLOWED = re.compile(r"^notifications/threads/\d+$")
_TOOL_DENIED = re.compile(r"delete|trash|share_file", re.I)

VAULT_OPS: dict = {}  # name -> fn(cfg, payload) -> str; filled by agents at import


def proposal_id(agent: str, action: str, target: str, key: str = "") -> str:
    return "p_" + hashlib.sha1(f"{agent}|{action}|{target}|{key}".encode()).hexdigest()[:10]


def auto_count_today(conn, agent: str) -> int:
    today = datetime.now().astimezone().date().isoformat()
    return conn.execute("SELECT COUNT(*) FROM proposals WHERE agent = ? AND level = 'auto' AND substr(created, 1, 10) = ?",
                        (agent, today)).fetchone()[0]


def propose(cfg: Config, conn, policy: dict, *, agent: str, action: str, target: str, payload: dict,
            preview: str, reason: str = "", undo: str = "", key: str = "") -> tuple[str, str]:
    """Queue (or auto-run) one action. Returns (id, outcome)."""
    pid = proposal_id(agent, action, target, key)
    level = autonomy_level(policy, agent, action)
    if level == "off":
        return pid, "off"
    if conn.execute("SELECT 1 FROM proposals WHERE id = ?", (pid,)).fetchone():
        return pid, "duplicate"
    cap = int(policy.get("defaults", {}).get("daily_cap", 20))
    if level == "auto" and auto_count_today(conn, agent) >= cap:
        level = "approve"  # over the cap: fall back to asking
    with conn:
        conn.execute(
            "INSERT INTO proposals (id, agent, action, target, payload, preview, reason, undo, status, created, level)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
            (pid, agent, action, target, json.dumps(payload), preview, reason, undo, db.now_iso(), level),
        )
    if level == "auto":
        status, _ = execute(cfg, conn, pid)
        return pid, status
    return pid, "pending"


def get(conn, pid: str) -> dict | None:
    row = conn.execute("SELECT * FROM proposals WHERE id = ?", (pid,)).fetchone()
    return _view(row) if row else None


def _view(row) -> dict:
    d = dict(row)
    d["payload"] = json.loads(d["payload"] or "{}")
    d["safe"] = d["action"] in SAFE_ACTIONS
    return d


def listing(conn, status: str | None = "pending", limit: int = 100) -> list[dict]:
    if status:
        rows = conn.execute("SELECT * FROM proposals WHERE status = ? ORDER BY created DESC LIMIT ?", (status, limit))
    else:
        rows = conn.execute("SELECT * FROM proposals ORDER BY created DESC LIMIT ?", (limit,))
    return [_view(r) for r in rows]


def reject(conn, pid: str) -> bool:
    with conn:
        cur = conn.execute("UPDATE proposals SET status = 'rejected', decided = ? WHERE id = ? AND status = 'pending'",
                           (db.now_iso(), pid))
    return cur.rowcount == 1


def approve(cfg: Config, conn, pid: str) -> tuple[str, str]:
    p = get(conn, pid)
    if not p:
        return "missing", f"no proposal {pid}"
    if p["status"] != "pending":
        return p["status"], p["result"] or ""
    return execute(cfg, conn, pid)


def approve_safe(cfg: Config, conn) -> dict[str, str]:
    return {p["id"]: approve(cfg, conn, p["id"])[0] for p in listing(conn, "pending") if p["safe"]}


def _set(conn, pid: str, **fields) -> None:
    cols = ", ".join(f"{k} = ?" for k in fields)
    with conn:
        conn.execute(f"UPDATE proposals SET {cols} WHERE id = ?", (*fields.values(), pid))


def execute(cfg: Config, conn, pid: str) -> tuple[str, str]:
    p = get(conn, pid)
    payload = p["payload"]
    _set(conn, pid, status="approved", decided=p["decided"] or db.now_iso())
    try:
        kind = payload["executor"]
        if kind == "gh":
            result = _run_gh(payload["calls"])
        elif kind == "tool":
            result = _run_tool(cfg, conn, p["agent"], payload)
        elif kind == "vault":
            result = VAULT_OPS[payload["op"]](cfg, payload)
        elif kind == "claude_code":
            result = _run_claude_code(cfg, conn, p["agent"], payload)
        else:
            raise ValueError(f"unknown executor {kind!r}")
        status = "executed"
    except Exception as e:  # noqa: BLE001 - any failure is recorded on the proposal
        status, result = "failed", str(e)[:500]
    _set(conn, pid, status=status, executed=db.now_iso(), result=result)
    return status, result


def _check_gh(args: list[str]) -> None:
    if args[:1] == ["api"]:
        if not (args[1:3] == ["-X", "PATCH"] and len(args) == 4 and _GH_API_ALLOWED.match(args[3])):
            raise ValueError(f"gh api call not allowed: {args}")
    elif tuple(args[:2]) not in _GH_ALLOWED:
        raise ValueError(f"gh command not allowed: {args[:2]}")


def _run_gh(calls: list[list[str]]) -> str:
    for args in calls:
        _check_gh(args)
    out = []
    for args in calls:
        p = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=60)
        if p.returncode != 0:
            raise RuntimeError(f"gh {' '.join(args[:3])}: {p.stderr.strip()[:300]}")
        out.append((p.stdout.strip() or "ok")[:200])
    return "; ".join(out)


TOOL_SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}, "detail": {"type": "string"}},
               "required": ["ok", "detail"]}


def _run_tool(cfg: Config, conn, agent: str, payload: dict) -> str:
    tools = payload["tools"]
    if not tools or any(not t.startswith("mcp__claude_ai_") or _TOOL_DENIED.search(t) for t in tools):
        raise ValueError(f"tools not allowed: {tools}")
    prompt = (f"Do exactly this and nothing else: {payload['instruction']}\n"
              f"Input: {json.dumps(payload.get('input', {}))}\nReport ok and a one-line detail.")
    started = db.now_iso()
    r = claude.run(prompt, cwd=cfg.home, model=cfg.model("scout"), max_turns=6, allowed_tools=tools,
                   json_schema=TOOL_SCHEMA, settings=cfg.headless_settings, persist=False, timeout=180)
    db.record_run(conn, agent=f"exec-{agent}", model=cfg.model("scout"), started=started, result=r)
    s = r.structured if isinstance(r.structured, dict) else {}
    if not (r.ok and s.get("ok")):
        raise RuntimeError(s.get("detail") or r.error or "tool call failed")
    return s["detail"]


def _run_claude_code(cfg: Config, conn, agent: str, payload: dict) -> str:
    started = db.now_iso()
    tools = ("Read", "Edit", "Write", "Glob", "Grep", "Bash(git:*)", "Bash(gh pr create:*)", "Bash(npm test:*)",
             "Bash(pytest:*)", "Bash(python -m pytest:*)", "Bash(uv run:*)")
    r = claude.run(payload["prompt"], cwd=payload["cwd"], model=cfg.model("headless"), max_turns=60,
                   allowed_tools=tools, settings=cfg.headless_settings, persist=True, timeout=1800)
    db.record_run(conn, agent=f"exec-{agent}", model=cfg.model("headless"), started=started, result=r)
    if not r.ok:
        raise RuntimeError(r.error or "claude run failed")
    return r.text[-500:]
