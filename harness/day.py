"""The day's shape: focus windows, carry-over, plan and shutdown.

Skills hand plan/shutdown decisions over as JSON; this module validates them,
renders the daily note sections, and keeps the worklog the Sheets row comes from.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from . import activity, db, github, vault
from .config import Config
from .scouts import parse_local

FOCUS_PREFIX = "🎯"
_TASK = re.compile(r"^\s*- \[( |x|X)\] (.+?)\s*$")
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_JIRA_KEY = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")

WORKLOG_HEADERS = [
    "Date", "Day", "Top 3 planned", "Top 3 done", "Done %", "Planned focus h", "Meeting h",
    "Claude Code h", "Commits", "PRs merged", "PRs reviewed", "Jira moved", "Carry-over", "Tomorrow first", "Note",
]


def tasks(text: str) -> list[tuple[bool, str]]:
    found = []
    for line in text.splitlines():
        m = _TASK.match(line)
        if m:
            found.append((m.group(1).lower() == "x", m.group(2)))
    return found


def unchecked(text: str) -> list[str]:
    return [t for done, t in tasks(text) if not done]


def _minutes(a: datetime, b: datetime) -> int:
    return max(0, round((b - a).total_seconds() / 60))


def timed_events(events: list[dict], day: date) -> list[dict]:
    """Today's non-all-day events I haven't declined, with parsed times."""
    out = []
    for e in events:
        if e.get("all_day") or e.get("response") == "declined":
            continue
        try:
            start, end = parse_local(e["start"]), parse_local(e["end"])
        except (KeyError, ValueError):
            continue
        if start.date() == day:
            out.append({**e, "_start": start, "_end": end})
    return sorted(out, key=lambda e: e["_start"])


def work_bounds(cfg: Config, day: date) -> tuple[datetime, datetime]:
    start, end = cfg.section("schedule").get("work_hours", "09:00-18:30").split("-")
    return (datetime.combine(day, datetime.strptime(start, "%H:%M").time()),
            datetime.combine(day, datetime.strptime(end, "%H:%M").time()))


def meeting_minutes(events: list[dict], bounds: tuple[datetime, datetime]) -> int:
    ws, we = bounds
    return sum(_minutes(max(e["_start"], ws), min(e["_end"], we))
               for e in events if not e["title"].startswith(FOCUS_PREFIX) and e["_end"] > ws and e["_start"] < we)


def focus_windows(events: list[dict], bounds: tuple[datetime, datetime], now: datetime | None = None,
                  min_minutes: int = 45) -> list[dict]:
    ws, we = bounds
    cursor = ws
    if now and ws < now < we:
        cursor = (now + timedelta(minutes=14)).replace(second=0, microsecond=0)
        cursor -= timedelta(minutes=cursor.minute % 15)
    windows = []
    busy = sorted((e["_start"], e["_end"]) for e in events if not e["title"].startswith(FOCUS_PREFIX))
    for start, end in busy + [(we, we)]:
        start = min(start, we)
        if _minutes(cursor, start) >= min_minutes:
            windows.append({"start": cursor.strftime("%H:%M"), "end": start.strftime("%H:%M"),
                            "minutes": _minutes(cursor, start)})
        cursor = max(cursor, end)
    return windows


def _previous_note(cfg: Config, kind: str, day: date, back: int) -> tuple[date, str] | None:
    for i in range(1, back + 1):
        d = day - timedelta(days=i)
        path = vault.note_path(cfg, kind, d)
        if path.exists():
            return d, path.read_text(encoding="utf-8")
    return None


def carry_candidates(cfg: Config, day: date) -> dict:
    today_note = vault.note_path(cfg, "daily", day)
    carried = vault.read_section(today_note.read_text(encoding="utf-8"), "carried") if today_note.exists() else None
    if carried is not None:
        carry = unchecked(carried)
    else:
        prev = _previous_note(cfg, "daily", day, 7)
        carry = unchecked(prev[1]) if prev else []
    inbox = []
    for i in range(0, 7):
        path = vault.note_path(cfg, "inbox", day - timedelta(days=i))
        if path.exists():
            inbox += [re.sub(r" \(\d\d:\d\d\)$", "", t) for t in unchecked(path.read_text(encoding="utf-8"))]
    diary = _previous_note(cfg, "diary", day + timedelta(days=1), 3)
    return {"carry": carry, "inbox_todos": inbox, "open_loops": unchecked(diary[1]) if diary else []}


def _ref_md(ref: str | None, site: str | None) -> str:
    if not ref:
        return ""
    if ref.startswith("http"):
        return f"[link]({ref})"
    if site and _JIRA_KEY.match(ref):
        return f"[{ref}](https://{site}/browse/{ref})"
    return ref


def validate_plan(data: dict) -> dict:
    top3 = data.get("top3") or []
    if not 1 <= len(top3) <= 3 or not all(isinstance(t, dict) and t.get("title") for t in top3):
        raise ValueError("plan needs top3: 1-3 items, each with a title")
    for b in data.get("blocks") or []:
        if not (_HHMM.match(b.get("start", "")) and _HHMM.match(b.get("end", "")) and b["start"] < b["end"]):
            raise ValueError(f"bad block {b!r}: need start < end as HH:MM")
    energy = data.get("energy")
    if energy is not None and energy not in range(1, 6):
        raise ValueError("energy must be 1-5")
    return data


def render_plan(data: dict, site: str | None, at: datetime) -> str:
    lines = ["**Top 3**"]
    for t in data["top3"]:
        extra = [f"~{t['est_min']}m"] if t.get("est_min") else []
        if t.get("ref"):
            extra.append(_ref_md(t["ref"], site))
        lines.append(f"- [ ] {t['title']}" + (" · " + " · ".join(extra) if extra else ""))
    if data.get("blocks"):
        lines += ["", "**Blocks**"] + [f"- {b['start']}–{b['end']} {b['task']}" for b in data["blocks"]]
    if data.get("also"):
        lines += ["", "**Also**"] + [f"- [ ] {a}" for a in data["also"]]
    footer = [f"energy {data['energy']}/5"] if data.get("energy") else []
    footer.append(f"planned {at.strftime('%H:%M')}")
    return "\n".join(lines + ["", f"*{' · '.join(footer)}*"])


def top3_lines(plan_text: str) -> list[tuple[bool, str]]:
    block = plan_text.split("**Top 3**", 1)[-1].split("\n**", 1)[0] if "**Top 3**" in plan_text else ""
    return tasks(block)


def plan_progress(cfg: Config, day: date) -> tuple[int, int]:
    path = vault.note_path(cfg, "daily", day)
    if not path.exists():
        return 0, 0
    items = top3_lines(vault.read_section(path.read_text(encoding="utf-8"), "plan") or "")
    return sum(done for done, _ in items), len(items)


def planned_focus_minutes(data: dict) -> int:
    fmt = "%H:%M"
    return sum(_minutes(datetime.strptime(b["start"], fmt), datetime.strptime(b["end"], fmt))
               for b in data.get("blocks") or [])


def commit_plan(cfg: Config, conn, day: date, data: dict, now: datetime | None = None):
    data = validate_plan(data)
    now = now or datetime.now()
    path = vault.write_section(cfg, "daily", "plan", render_plan(data, cfg.section("jira").get("site"), now), day)
    db.save_worklog(conn, day.isoformat(), planned={**data, "focus_min": planned_focus_minutes(data),
                                                     "at": now.isoformat(timespec="minutes")})
    return path


def actuals(cfg: Config, day: date, today: dict | None) -> dict:
    path = vault.note_path(cfg, "daily", day)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    plan_text = vault.read_section(text, "plan") or ""
    sessions = activity.sessions(day)
    meetings = 0
    if today and today.get("date") == day.isoformat():
        meetings = meeting_minutes(timed_events(today.get("events", []), day), work_bounds(cfg, day))
    return {
        "date": day.isoformat(),
        "top3": [{"title": t.split(" · ")[0], "done": done} for done, t in top3_lines(plan_text)],
        "also": [{"title": t, "done": done} for done, t in tasks(plan_text.split("**Also**", 1)[1])] if "**Also**" in plan_text else [],
        "github": github.day_activity(cfg.section("github").get("user", ""), day),
        "claude_code": {"active_min": sum(s["active_min"] for s in sessions), "by_project": activity.by_project(sessions)},
        "meeting_min": meetings,
    }


def _mark_done(plan_text: str, done: list[str]) -> str:
    wanted = [d.strip().lower() for d in done]
    out = []
    for line in plan_text.splitlines():
        m = _TASK.match(line)
        if m and m.group(1) == " " and m.group(2).split(" · ")[0].strip().lower() in wanted:
            line = line.replace("- [ ]", "- [x]", 1)
        out.append(line)
    return "\n".join(out)


def _hours(minutes: int) -> str:
    return f"{minutes / 60:.1f}h"


def render_shutdown(data: dict, actual: dict, at: datetime) -> str:
    gh = actual.get("github", {})
    cc = actual.get("claude_code", {})
    top3 = actual.get("top3", [])
    done_count = sum(1 for t in top3 if t["done"])
    lines = [f"**Top 3 done**: {done_count}/{len(top3)}" if top3 else "**No plan was committed today**"]
    if data.get("done"):
        lines += [f"- [x] {d}" for d in data["done"]]
    if data.get("carry"):
        lines += ["", "**Carried to tomorrow**"] + [f"- {c}" for c in data["carry"]]
    if data.get("tomorrow_first"):
        lines += ["", f"**Tomorrow starts with**: {data['tomorrow_first']}"]
    if data.get("wins"):
        lines += ["", "**Wins**"] + [f"- {w}" for w in data["wins"]]
    projects = ", ".join(f"{p} {_hours(m)}" for p, m in list(cc.get("by_project", {}).items())[:4])
    time_line = [f"meetings {_hours(actual.get('meeting_min', 0))}",
                 f"Claude Code {_hours(cc.get('active_min', 0))}" + (f" ({projects})" if projects else ""),
                 f"{len(gh.get('commits', []))} commits", f"{len(gh.get('merged', []))} PRs merged",
                 f"{len(gh.get('reviewed', []))} reviews"]
    if data.get("jira_moved") is not None:
        time_line.append(f"{data['jira_moved']} Jira moved")
    lines += ["", "**Time**: " + " · ".join(time_line)]
    if data.get("note"):
        lines += ["", data["note"]]
    return "\n".join(lines + ["", f"*shut down {at.strftime('%H:%M')}*"])


def commit_shutdown(cfg: Config, conn, day: date, data: dict, actual: dict, now: datetime | None = None) -> list:
    now = now or datetime.now()
    path = vault.ensure_note(cfg, "daily", day)
    text = path.read_text(encoding="utf-8")
    plan_text = vault.read_section(text, "plan")
    if plan_text is not None and data.get("done"):
        text = vault.replace_section(text, "plan", _mark_done(plan_text, data["done"]))
        actual = {**actual, "top3": [{"title": t.split(" · ")[0], "done": d}
                                     for d, t in top3_lines(vault.read_section(text, "plan") or "")]}
    text = vault.replace_section(text, "shutdown", render_shutdown(data, actual, now))
    vault.atomic_write(path, text)
    written = [path]
    carry = list(data.get("carry") or [])
    first = data.get("tomorrow_first")
    if first and first not in carry:
        carry.insert(0, first)
    elif first:
        carry.insert(0, carry.pop(carry.index(first)))
    if carry:
        written.append(vault.write_section(cfg, "daily", "carried", "\n".join(f"- [ ] {c}" for c in carry),
                                           day + timedelta(days=1)))
    db.save_worklog(conn, day.isoformat(), actual={**actual, **{k: data.get(k) for k in
                    ("done", "carry", "tomorrow_first", "wins", "jira_moved", "note")},
                    "at": now.isoformat(timespec="minutes")})
    return written


def worklog_row(day: str, planned: dict, actual: dict) -> list:
    top3 = actual.get("top3") or [{"title": t["title"], "done": False} for t in planned.get("top3", [])]
    done = sum(1 for t in top3 if t["done"])
    gh = actual.get("github", {})
    weekday = date.fromisoformat(day).strftime("%a")
    return [
        day, weekday,
        "; ".join(t["title"] for t in top3),
        done,
        round(100 * done / len(top3)) if top3 else 0,
        round(planned.get("focus_min", 0) / 60, 1),
        round(actual.get("meeting_min", 0) / 60, 1),
        round(actual.get("claude_code", {}).get("active_min", 0) / 60, 1),
        len(gh.get("commits", [])),
        len(gh.get("merged", [])),
        len(gh.get("reviewed", [])),
        actual.get("jira_moved") or 0,
        "; ".join(actual.get("carry") or []),
        actual.get("tomorrow_first") or "",
        actual.get("note") or "",
    ]
