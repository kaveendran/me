"""`me doctor`: check every integration and print ✓ / ! / ✗."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import claude, db, schedule, sheets
from .bootstrap import git_remotes, public_gitignore_missing
from .config import HOME, ConfigError, load, load_autonomy

OK, WARN, FAIL = "✓", "!", "✗"

LIVE_SCHEMA = {
    "type": "object",
    "properties": {
        "calendar_ok": {"type": "boolean"},
        "atlassian_ok": {"type": "boolean"},
        "drive_ok": {"type": "boolean"},
        "errors": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["calendar_ok", "atlassian_ok", "drive_ok", "errors"],
}
LIVE_TOOLS = (
    "mcp__claude_ai_Google_Calendar__list_calendars",
    "mcp__claude_ai_Atlassian__getAccessibleAtlassianResources",
    "mcp__claude_ai_Google_Drive__search_files",
)


def _cmd(*args: str, timeout: float = 15) -> tuple[int, str]:
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def _openwiki_version() -> str | None:
    exe = shutil.which("openwiki")
    if not exe:
        return None
    d = Path(os.path.realpath(exe)).parent
    while d != d.parent:
        pkg = d / "package.json"
        if pkg.exists():
            return json.loads(pkg.read_text()).get("version", "?")
        d = d.parent
    return "?"


def checks(live: bool = False):
    yield (OK if sys.version_info >= (3, 11) else FAIL), "python", sys.version.split()[0]

    try:
        cfg = load()
        yield OK, "me.toml", str(HOME / "me.toml")
    except ConfigError as e:
        yield FAIL, "me.toml", str(e)
        return
    try:
        load_autonomy()
        yield OK, "autonomy.toml", "valid"
    except (ConfigError, OSError) as e:
        yield FAIL, "autonomy.toml", str(e)

    code, out = _cmd("claude", "--version")
    yield (OK if code == 0 else FAIL), "claude CLI", out.splitlines()[0] if out else "not found"

    code, out = _cmd("gh", "auth", "status")
    account = next((l.split("account")[-1].split()[0] for l in out.splitlines() if "Logged in" in l), "")
    yield (OK if code == 0 else FAIL), "github (gh)", f"logged in as {account}" if code == 0 else out[:120]

    version = _openwiki_version()
    yield (OK if version else WARN), "openwiki", f"v{version}" if version else "not installed"

    for zone in cfg.zones.values():
        label = f"vault:{zone.name}"
        if not zone.vault.is_dir():
            yield FAIL, label, f"{zone.vault} missing (run `me init`)"
            continue
        if not os.access(zone.vault, os.W_OK):
            yield FAIL, label, f"{zone.vault} not writable"
            continue
        if zone.public:
            missing = public_gitignore_missing(cfg)
            rules = missing.get(zone.vault / ".gitignore")
            if rules:
                yield WARN, label, f"public; .gitignore lacks {' '.join(rules)} (run `me vault guard-public`)"
            else:
                yield OK, label, f"public, private folders gitignored ({zone.vault})"
        else:
            remotes = git_remotes(zone.vault)
            if remotes:
                yield FAIL, label, f"private vault has git remotes {remotes}; it must stay local"
            else:
                yield OK, label, f"private, local-only ({zone.vault})"

    try:
        conn = db.connect(cfg.db_path)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        yield OK, "state db", f"schema v{version} ({cfg.db_path})"
    except Exception as e:  # noqa: BLE001 - doctor reports anything
        yield FAIL, "state db", str(e)

    jira = cfg.section("jira")
    yield (OK if jira.get("cloud_id") else WARN), "jira config", jira.get("site") or "site/cloud_id not set"
    ntfy = cfg.section("ntfy")
    yield (OK if ntfy.get("topic") else WARN), "ntfy topic", "configured" if ntfy.get("topic") else "not set"
    sheets_cfg = cfg.section("sheets")
    have = [k for k in ("worklog_id", "okr_id") if sheets_cfg.get(k)]
    yield (OK if len(have) == 2 else WARN), "sheets", f"{len(have)}/2 created" if have else "run `me sheets init`"
    try:
        conn = db.connect(cfg.db_path)
        last = conn.execute("SELECT status, error FROM runs WHERE agent = 'sheets-sync' ORDER BY id DESC LIMIT 1").fetchone()
        pending = len(sheets.pending(conn))
    except Exception:  # noqa: BLE001
        last, pending = None, 0
    if last and last["error"] and "connector not enabled" in last["error"]:
        yield WARN, "sheets sync", f"Google Sheets connector off; {pending} day(s) waiting locally"
    elif pending:
        yield WARN, "sheets sync", f"{pending} day(s) waiting; run `me sheets sync`"

    jobs = schedule.status(cfg)
    installed = [j for j in jobs if "not installed" not in j]
    yield (OK if len(installed) == len(jobs) else WARN), "schedule", f"{len(installed)}/{len(jobs)} launchd jobs installed"
    if not (cfg.home / ".claude" / "settings.json").exists():
        yield WARN, "project settings", "plain `claude` here still records to Cognee; cp config/claude-settings.json .claude/settings.json"

    if live:
        r = claude.run(
            "Connectivity check. Call list_calendars, getAccessibleAtlassianResources, and Google Drive "
            "search_files for one spreadsheet. Call nothing else. Report in the schema.",
            cwd=cfg.home, model=cfg.model("scout"), max_turns=8, allowed_tools=LIVE_TOOLS, json_schema=LIVE_SCHEMA,
            settings=cfg.headless_settings, persist=False, timeout=180,
        )
        s = r.structured or {}
        for key, name in (("calendar_ok", "calendar"), ("atlassian_ok", "jira"), ("drive_ok", "drive/sheets")):
            yield (OK if s.get(key) else FAIL), f"live:{name}", "headless connector ok" if s.get(key) else (r.error or "; ".join(s.get("errors", [])))
        yield OK, "live:cost", f"${r.cost_usd:.3f}, {r.turns} turns"


def main(live: bool = False) -> int:
    failed = False
    for mark, name, detail in checks(live):
        print(f" {mark} {name:<16} {detail}")
        failed |= mark == FAIL
    return 1 if failed else 0
