"""Time spent in Claude Code per project, read from local session transcripts.

No hook needed: every session already writes ~/.claude/projects/<slug>/<id>.jsonl
with timestamped events. Active time sums the gaps between consecutive
user/assistant events, capping each gap so idle time doesn't count.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from itertools import pairwise
from pathlib import Path

PROJECTS = Path.home() / ".claude" / "projects"
IDLE_CAP = timedelta(minutes=5)


def _local(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)


def _session_stamps(day: date, root: Path):
    """Yield (path, cwd, sorted local timestamps) for each human session active on `day`."""
    start = datetime.combine(day, time.min)
    end = start + timedelta(days=1)
    for path in root.glob("*/*.jsonl"):  # top level only, so subagent transcripts are skipped
        if datetime.fromtimestamp(path.stat().st_mtime) < start:
            continue
        stamps, cwd, entrypoint = [], None, None
        with path.open(encoding="utf-8", errors="ignore") as f:
            for line in f:
                if '"timestamp"' not in line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                entrypoint = event.get("entrypoint") or entrypoint
                cwd = event.get("cwd") or cwd
                if event.get("type") not in ("user", "assistant") or event.get("isSidechain"):
                    continue
                ts = _local(event["timestamp"])
                if start <= ts < end:
                    stamps.append(ts)
        if len(stamps) < 2 or (entrypoint or "").startswith("sdk"):
            continue
        yield path, cwd, sorted(stamps)


def _active(stamps: list[datetime]) -> timedelta:
    return sum((min(b - a, IDLE_CAP) for a, b in pairwise(stamps)), timedelta())


def sessions(day: date, root: Path = PROJECTS) -> list[dict]:
    found = []
    for path, cwd, stamps in _session_stamps(day, root):
        found.append({
            "session": path.stem,
            "project": Path(cwd).name if cwd else path.parent.name,
            "first": stamps[0].strftime("%H:%M"),
            "last": stamps[-1].strftime("%H:%M"),
            "active_min": round(_active(stamps).total_seconds() / 60),
        })
    return found


def timeline(day: date, root: Path = PROJECTS) -> list[datetime]:
    """Every human Claude Code event on `day`, across sessions, sorted."""
    return sorted(ts for _, _, stamps in _session_stamps(day, root) for ts in stamps)


def streaks(stamps: list[datetime], gap: timedelta = timedelta(minutes=10)) -> list[tuple[datetime, datetime]]:
    """Continuous work stretches: runs of events with no gap longer than `gap`."""
    runs: list[tuple[datetime, datetime]] = []
    for ts in stamps:
        if runs and ts - runs[-1][1] <= gap:
            runs[-1] = (runs[-1][0], ts)
        else:
            runs.append((ts, ts))
    return runs


def by_project(found: list[dict]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for s in found:
        totals[s["project"]] = totals.get(s["project"], 0) + s["active_min"]
    return dict(sorted(totals.items(), key=lambda kv: -kv[1]))
