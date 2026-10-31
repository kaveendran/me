"""`me brief`: gather the day in parallel, let the model pick a Top 3, render it.

Gathering is mostly deterministic (gh, the vault, transcripts). Calendar, Jira and
OKRs go through connector scouts. One synthesis call does the judgment: Top 3,
headline, risks, suggested blocks. Python validates and renders everything.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

from . import claude, db, day as daymod, github, proposals, scouts, today as todaymod, vault
from .config import Config

SYNTH_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "top3": {"type": "array", "maxItems": 3, "items": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "why": {"type": "string"},
                "ref": {"type": "string"},
                "est_min": {"type": "integer"},
                "source": {"type": "string", "enum": ["jira", "github", "carry", "inbox", "calendar", "okr", "other"]},
            },
            "required": ["title", "why", "source"],
        }},
        "blocks": {"type": "array", "items": {
            "type": "object",
            "properties": {"start": {"type": "string"}, "end": {"type": "string"}, "task": {"type": "string"}},
            "required": ["start", "end", "task"],
        }},
        "risks": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["headline", "top3", "blocks", "risks"],
}

SYNTH_PROMPT = """You are {name}'s chief of staff writing the morning brief for {weekday} {date}.

Pick today's Top 3 from the data below (fewer if there isn't enough real work). Priority order:
1. things blocking other people: non-stale review requests, failing CI on their own open PRs;
2. Jira issues already In Progress, then high priority or due soon;
3. carry-over from yesterday, then inbox todos;
prefer work that moves a key result when KRs are given. Items with "stale": true are not candidates; if
there are many, mention them once as a risk.

- title: a short action phrase. why: at most 12 words. ref: the Jira key or URL when there is one.
  est_min: realistic minutes. source: where it came from.
- blocks: schedule Top 3 work only inside focus_windows (same day, HH:MM), no overlaps, 30-120 min each.
- headline: one sentence, at most 20 words, on the shape of the day.
- risks: up to 3 short items (overload, conflicts, things slipping); empty if none.
Use no tools.

DATA
{data}"""


def _compact(gathered: dict) -> dict:
    gh = gathered["github"]
    return {
        "events_today": [{"title": e["title"], "start": e["_start"].strftime("%H:%M"), "end": e["_end"].strftime("%H:%M"),
                          "attendees": e.get("attendees", 0)} for e in gathered["events"]],
        "focus_windows": gathered["focus_windows"],
        "meeting_hours": round(gathered["meeting_min"] / 60, 1),
        "review_requests": [{k: r[k] for k in ("repo", "number", "title", "url", "author", "stale")} for r in gh["reviews"]],
        "my_open_prs": [{k: p.get(k) for k in ("repo", "number", "title", "url", "draft", "stale", "ci")} for p in gh["my_prs"]],
        "assigned_issues": [{k: i[k] for k in ("repo", "number", "title", "url", "stale")} for i in gh["issues"]],
        "jira": gathered["jira"],
        "carry_over": gathered["vault"]["carry"],
        "inbox_todos": gathered["vault"]["inbox_todos"],
        "key_results": gathered["okr"],
    }


def gather(cfg: Config, day: date, now: datetime) -> dict:
    repos = cfg.section("github").get("repos", [])
    with ThreadPoolExecutor(max_workers=4) as pool:
        f_gh = pool.submit(github.snapshot, repos)
        f_cal = pool.submit(scouts.calendar, cfg, day)
        f_jira = pool.submit(scouts.jira, cfg)
        f_okr = pool.submit(scouts.okr, cfg)
        vault_data = daymod.carry_candidates(cfg, day)
        gh, cal, jira, okr = f_gh.result(), f_cal.result(), f_jira.result(), f_okr.result()
    all_events = cal.get("events", [])
    events = daymod.timed_events(all_events, day)
    bounds = daymod.work_bounds(cfg, day)
    tomorrow = daymod.timed_events(all_events, day + timedelta(days=1))[:3]
    return {
        "github": gh, "events": events, "tomorrow": tomorrow,
        "all_day": [e["title"] for e in all_events if e.get("all_day") and str(e.get("start", "")).startswith(day.isoformat())],
        "focus_windows": daymod.focus_windows(events, bounds, now if now.date() == day else None),
        "meeting_min": daymod.meeting_minutes(events, bounds),
        "jira": jira.get("issues", []), "okr": okr.get("rows", []), "vault": vault_data,
        "errors": gh.get("errors", []) + cal.get("errors", []) + jira.get("errors", []) + okr.get("errors", []),
    }


def synthesize(cfg: Config, conn, day: date, gathered: dict) -> dict:
    prompt = SYNTH_PROMPT.format(name=cfg.name, weekday=day.strftime("%A"), date=day.isoformat(),
                                 data=json.dumps(_compact(gathered), indent=1, default=str))
    started = db.now_iso()
    result = claude.run(prompt, cwd=cfg.home, model=cfg.model("headless"), max_turns=2, json_schema=SYNTH_SCHEMA,
                        settings=cfg.headless_settings, persist=False, mcp=False, timeout=300)
    db.record_run(conn, agent="brief-synth", model=cfg.model("headless"), started=started, result=result)
    out = result.structured if isinstance(result.structured, dict) else {}
    if not result.ok:
        gathered["errors"].append(f"synthesis: {result.error}")
    windows = gathered["focus_windows"]
    out["blocks"] = [b for b in out.get("blocks", [])
                     if any(w["start"] <= b["start"] < b["end"] <= w["end"] for w in windows)]
    out["top3"] = out.get("top3", [])[:3]
    return out


def _event_view(e: dict) -> dict:
    return {"id": e.get("id"), "title": e["title"], "start": e["_start"].strftime("%H:%M"),
            "end": e["_end"].strftime("%H:%M"), "attendees": e.get("attendees", 0),
            "attendee_names": e.get("attendee_names", []), "organizer_self": e.get("organizer_self", False),
            "meet_url": e.get("meet_url") or "", "response": e.get("response")}


def run(cfg: Config, conn, day: date | None = None, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    day = day or now.date()
    gathered = gather(cfg, day, now)
    synth = synthesize(cfg, conn, day, gathered)
    gh = gathered["github"]
    today = {
        "date": day.isoformat(),
        "generated_at": now.isoformat(timespec="minutes"),
        "headline": synth.get("headline", ""),
        "top3": synth.get("top3", []),
        "blocks": synth.get("blocks", []),
        "risks": synth.get("risks", []),
        "events": [_event_view(e) for e in gathered["events"]],
        "tomorrow": [_event_view(e) for e in gathered["tomorrow"]],
        "all_day": gathered["all_day"],
        "focus_windows": gathered["focus_windows"],
        "meeting_min": gathered["meeting_min"],
        "reviews": gh["reviews"], "my_prs": gh["my_prs"], "issues": gh["issues"],
        "jira": gathered["jira"], "okr": gathered["okr"], **gathered["vault"],
        "errors": gathered["errors"],
        "proposals_pending": len(proposals.listing(conn, "pending")),
    }
    todaymod.save(cfg, today)
    vault.write_section(cfg, "daily", "brief", render(today, cfg.section("jira").get("site")), day)
    return today


def _link(title: str, url: str | None) -> str:
    return f"[{title}]({url})" if url else title


def render(t: dict, site: str | None) -> str:
    d = date.fromisoformat(t["date"])
    gen = t["generated_at"][11:16]
    lines = [f"> [!abstract] Brief · {d.strftime('%a %d %b')} · {gen}", f"> **{t['headline'] or 'No headline.'}**", ""]
    fresh_reviews = [r for r in t["reviews"] if not r["stale"]]
    failing = [p for p in t["my_prs"] if p.get("ci") == "failing"]
    summary = [f"{len(t['events'])} meetings ({t['meeting_min'] / 60:.1f}h)"]
    if t["focus_windows"]:
        summary.append("focus " + ", ".join(f"{w['start']}–{w['end']}" for w in t["focus_windows"]))
    if fresh_reviews:
        summary.append(f"{len(fresh_reviews)} reviews waiting")
    if failing:
        summary.append(f"{len(failing)} PR failing CI")
    lines += ["**Today**: " + " · ".join(summary), ""]
    if t["top3"]:
        lines.append("**Proposed Top 3** (run `me plan` to commit)")
        for i, item in enumerate(t["top3"], 1):
            ref = daymod.ref_md(item.get("ref"), site)
            est = f" · ~{item['est_min']}m" if item.get("est_min") else ""
            lines.append(f"{i}. {item['title']}: {item['why']}{est}{' · ' + ref if ref else ''}")
        lines.append("")
    if t["events"] or t["all_day"]:
        lines.append("**Schedule**")
        lines += [f"- all day: {title}" for title in t["all_day"]]
        lines += [f"- {e['start']}–{e['end']} {e['title']}" + (f" ({e['attendees']})" if e["attendees"] > 1 else "")
                  for e in t["events"]]
        lines.append("")
    if fresh_reviews:
        lines.append("**Reviews waiting**: " + ", ".join(_link(f"{r['repo']}#{r['number']}", r["url"]) for r in fresh_reviews))
    mine = [p for p in t["my_prs"] if not p["stale"]]
    if mine:
        marks = {"failing": " ✗ CI", "pending": " ⋯ CI", "passing": " ✓"}
        lines.append("**Your PRs**: " + ", ".join(_link(f"{p['repo']}#{p['number']}", p["url"]) + marks.get(p.get("ci"), "")
                                                    for p in mine))
    active = [j for j in t["jira"] if j.get("status_category") == "In Progress"]
    todo = [j for j in t["jira"] if j.get("status_category") == "To Do"]
    if active or todo:
        lines.append("**Jira**: " + "; ".join(filter(None, [
            ("in progress " + ", ".join(daymod.ref_md(j["key"], site) for j in active)) if active else "",
            ("to do " + ", ".join(daymod.ref_md(j["key"], site) for j in todo[:6])) if todo else "",
        ])))
    if t.get("proposals_pending"):
        lines.append(f"**Approvals waiting**: {t['proposals_pending']} (`me proposals` or Mission Control)")
    if t["carry"]:
        lines.append("**Carried over**: " + "; ".join(t["carry"][:6]))
    if t["inbox_todos"]:
        lines.append("**Inbox todos**: " + "; ".join(t["inbox_todos"][:6]))
    stale = sum(1 for r in t["reviews"] if r["stale"]) + sum(1 for p in t["my_prs"] if p["stale"])
    if stale:
        lines.append(f"**Stale GitHub items**: {stale} (untouched 30+ days)")
    if t["risks"]:
        lines += ["", "**Risks**"] + [f"- {r}" for r in t["risks"]]
    if t["errors"]:
        lines += ["", "> [!warning]- Gathering problems", *[f"> - {e}" for e in t["errors"]]]
    return "\n".join(lines).rstrip()
