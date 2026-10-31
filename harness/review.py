"""`me review`: the weekly review. What happened, wins and slips, KR progress,
wellbeing patterns, and one small experiment for next week."""

from __future__ import annotations

import json
from datetime import date, timedelta

from . import db, github, notify, scouts, vault, wellbeing
from .agents import judge
from .config import Config

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "wins": {"type": "array", "items": {"type": "string"}},
        "slips": {"type": "array", "items": {"type": "string"}},
        "kr_notes": {"type": "array", "items": {"type": "object", "properties": {
            "key_result": {"type": "string"}, "note": {"type": "string"}, "suggested_current": {"type": "string"}},
            "required": ["key_result", "note"]}},
        "wellbeing": {"type": "string"},
        "drains": {"type": "array", "items": {"type": "string"}},
        "energizers": {"type": "array", "items": {"type": "string"}},
        "experiment": {"type": "string"},
    },
    "required": ["summary", "wins", "slips", "kr_notes", "wellbeing", "drains", "energizers", "experiment"],
}

PROMPT = """Write kaveendran's weekly review for the week starting {monday}. Be specific and kind, never
grading. Use only the data.

- summary: 3-4 sentences on what the week was actually about.
- wins and slips: concrete, at most 5 each. A slip is a plan that didn't happen, not a character flaw.
- kr_notes: for each key result, what moved this week (suggested_current only when the data clearly shows a value).
- wellbeing: two sentences relating mood/energy/load to what happened (meeting load, late nights, long stretches).
  No clinical language, no diagnosis.
- drains / energizers: patterns visible in the data.
- experiment: ONE small, concrete thing to try next week that targets the biggest drain.
Use no tools.

DATA
{data}"""


def week_bounds(day: date) -> tuple[date, date]:
    monday = day - timedelta(days=day.weekday())
    return monday, monday + timedelta(days=6)


def run(cfg: Config, conn, day: date | None = None) -> dict:
    day = day or date.today()
    monday, sunday = week_bounds(day)
    days = [monday + timedelta(days=i) for i in range((min(day, sunday) - monday).days + 1)]
    daily = []
    for d in days:
        planned, actual = db.get_worklog(conn, d.isoformat())
        note = vault.note_path(cfg, "daily", d)
        shutdown = vault.read_section(note.read_text(encoding="utf-8"), "shutdown") if note.exists() else None
        s = wellbeing.signals(cfg, conn, d)
        daily.append({"date": d.isoformat(), "planned_top3": [t["title"] for t in planned.get("top3", [])],
                      "top3": actual.get("top3"), "wins": actual.get("wins"), "carry": actual.get("carry"),
                      "shutdown": shutdown, "signals": s, "load": wellbeing.load_score(s)})
    try:
        merged = [f"{p['repo']}#{p['number']} {p['title']}" for p in github.merged_since(monday)]
    except github.GhError:
        merged = []
    jira_done = scouts.jira_detail(cfg, "assignee = currentUser() AND statusCategory = Done AND resolved >= startOfWeek()")
    okr = scouts.okr(cfg)
    data = {"days": daily, "merged_prs": merged, "jira_done": [f"{i['key']} {i['summary']}" for i in jira_done.get("issues", [])],
            "key_results": okr.get("rows", [])}
    out = judge(cfg, conn, "review", PROMPT.format(monday=monday, data=json.dumps(data, indent=1, default=str)), REVIEW_SCHEMA)

    def bullets(items):
        return "\n".join(f"- {i}" for i in items) or "_none_"

    vault.write_section(cfg, "reviews", "review", f"{out['summary']}\n\n**Wins**\n{bullets(out['wins'])}\n\n**Slips**\n{bullets(out['slips'])}", day)
    vault.write_section(cfg, "reviews", "okr", bullets(f"**{k['key_result']}**: {k['note']}"
                                                      + (f" (now ~{k['suggested_current']})" if k.get("suggested_current") else "")
                                                      for k in out["kr_notes"]), day)
    vault.write_section(cfg, "reviews", "wellbeing", f"{out['wellbeing']}\n\n**Drains**\n{bullets(out['drains'])}\n\n"
                                                    f"**Energizers**\n{bullets(out['energizers'])}", day)
    vault.write_section(cfg, "reviews", "experiment", out["experiment"], day)
    db.set_cursor(conn, "experiment", out["experiment"])
    notify.send(cfg, conn, "review", "Week review ready", out["experiment"][:180], tags=["calendar"])
    return out
