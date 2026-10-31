"""Google Sheets: the Work Log and Goals/OKR spreadsheets.

The worklog table in state/me.db is the source of truth; Sheets is a sync target.
Creating the files only needs the Drive connector. Writing rows needs the Google
Sheets connector; without it `sync` stops at session init, before any model call.
"""

from __future__ import annotations

import csv
import io
import json

from . import claude, db
from .config import Config, set_value
from .day import WORKLOG_HEADERS, worklog_row

SHEETS_PREFIX = "mcp__claude_ai_Google_Sheets"
SHEETS_CONNECTOR = "claude.ai Google Sheets"
OKR_HEADERS = ["Objective", "Key result", "Start", "Target", "Current", "Progress %", "Confidence (1-10)", "Updated", "Notes"]

INIT_SCHEMA = {
    "type": "object",
    "properties": {"worklog_id": {"type": "string"}, "okr_id": {"type": "string"},
                   "errors": {"type": "array", "items": {"type": "string"}}},
    "required": ["worklog_id", "okr_id", "errors"],
}
SYNC_SCHEMA = {
    "type": "object",
    "properties": {"written_days": {"type": "array", "items": {"type": "string"}},
                   "errors": {"type": "array", "items": {"type": "string"}}},
    "required": ["written_days", "errors"],
}


def url(file_id: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{file_id}/edit"


def csv_text(rows: list[list]) -> str:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    return buf.getvalue()


def init(cfg: Config, conn) -> dict:
    s = cfg.section("sheets")
    if s.get("worklog_id") and s.get("okr_id"):
        return {"worklog_id": s["worklog_id"], "okr_id": s["okr_id"], "created": False}
    prompt = (
        "Create two Google Sheets with create_file by uploading CSV text (contentMimeType text/csv) so each converts "
        "to a spreadsheet. Return both file ids in the schema. Call nothing except create_file.\n"
        f"1. title: me · Work Log\ntextContent:\n{csv_text([WORKLOG_HEADERS])}\n"
        f"2. title: me · Goals & OKRs\ntextContent:\n{csv_text([OKR_HEADERS])}"
    )
    started = db.now_iso()
    result = claude.run(prompt, cwd=cfg.home, model=cfg.model("scout"), max_turns=5,
                        allowed_tools=("mcp__claude_ai_Google_Drive__create_file",), json_schema=INIT_SCHEMA,
                        settings=cfg.headless_settings, persist=False, timeout=240)
    db.record_run(conn, agent="sheets-init", model=cfg.model("scout"), started=started, result=result)
    out = result.structured if isinstance(result.structured, dict) else {}
    if not (result.ok and out.get("worklog_id") and out.get("okr_id")):
        raise RuntimeError(f"sheet creation failed: {result.error or out.get('errors')}")
    set_value(cfg.home / "me.toml", "sheets", "worklog_id", out["worklog_id"])
    set_value(cfg.home / "me.toml", "sheets", "okr_id", out["okr_id"])
    return {**out, "created": True}


def pending(conn) -> list[tuple[str, list]]:
    rows = conn.execute("SELECT day, planned, actual FROM worklog WHERE synced IS NULL ORDER BY day").fetchall()
    return [(r["day"], worklog_row(r["day"], json.loads(r["planned"] or "{}"), json.loads(r["actual"] or "{}")))
            for r in rows]


def sync(cfg: Config, conn) -> dict:
    sheet_id = cfg.section("sheets").get("worklog_id")
    if not sheet_id:
        return {"status": "skipped", "detail": "no worklog_id; run `me sheets init`"}
    rows = pending(conn)
    if not rows:
        return {"status": "ok", "detail": "nothing to sync"}
    prompt = (
        f"Spreadsheet id {sheet_id}, first tab. Row 1 is the header {json.dumps(WORKLOG_HEADERS)}; column A holds dates "
        "(YYYY-MM-DD). Read column A first. For each row below: if a row with that date exists, overwrite that row; "
        "otherwise append it after the last used row. Write with valueInputOption RAW so nothing is reinterpreted, "
        "keeping numbers as numbers. Then read back the written rows to verify, and report the dates you wrote.\n"
        f"ROWS:\n{json.dumps([r for _, r in rows], ensure_ascii=False)}"
    )
    started = db.now_iso()
    result = claude.run(prompt, cwd=cfg.home, model=cfg.model("headless"), max_turns=10,
                        allowed_tools=(SHEETS_PREFIX,), require_mcp=SHEETS_CONNECTOR, json_schema=SYNC_SCHEMA,
                        settings=cfg.headless_settings, persist=False, timeout=300)
    db.record_run(conn, agent="sheets-sync", model=cfg.model("headless"), started=started, result=result)
    if not result.ok:
        hint = " Turn on the Google Sheets connector in claude.ai settings." if "connector not enabled" in (result.error or "") else ""
        return {"status": "pending", "detail": f"{len(rows)} day(s) kept locally: {result.error}.{hint}"}
    written = set((result.structured or {}).get("written_days", []))
    with conn:
        for day, _ in rows:
            if day in written:
                conn.execute("UPDATE worklog SET synced = ? WHERE day = ?", (db.now_iso(), day))
    missing = [d for d, _ in rows if d not in written]
    return {"status": "ok" if not missing else "partial",
            "detail": f"synced {len(written)} day(s)" + (f"; still pending {missing}" if missing else "")}
