"""Connector scouts: headless claude runs that fetch Calendar, Jira and OKR data.

The model is only the transport here: each scout gets exactly the read-only
tools it needs, returns JSON matching a schema, and the controller does the
rest. Scouts don't persist sessions, so they don't count as Claude Code activity.
"""

from __future__ import annotations

from contextlib import closing
from datetime import date, datetime, timedelta

from . import claude, db
from .config import Config

CALENDAR_SCHEMA = {
    "type": "object",
    "properties": {
        "events": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "title": {"type": "string"},
                "start": {"type": "string", "description": "local YYYY-MM-DDTHH:MM, or YYYY-MM-DD if all-day"},
                "end": {"type": "string"},
                "all_day": {"type": "boolean"},
                "attendees": {"type": "integer"},
                "organizer_self": {"type": "boolean"},
                "attendee_names": {"type": "array", "items": {"type": "string"}, "description": "display names or emails, max 12"},
                "response": {"type": "string", "enum": ["accepted", "tentative", "needsAction", "declined", "none"]},
                "meet_url": {"type": "string"},
                "location": {"type": "string"},
            },
            "required": ["id", "title", "start", "end", "all_day", "response"],
        }},
        "errors": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["events", "errors"],
}

JIRA_SCHEMA = {
    "type": "object",
    "properties": {
        "issues": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "summary": {"type": "string"},
                "status": {"type": "string"},
                "status_category": {"type": "string", "enum": ["To Do", "In Progress", "Done"]},
                "priority": {"type": "string"},
                "type": {"type": "string"},
                "updated": {"type": "string"},
                "due": {"type": "string"},
            },
            "required": ["key", "summary", "status", "status_category"],
        }},
        "errors": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["issues", "errors"],
}

OKR_SCHEMA = {
    "type": "object",
    "properties": {
        "rows": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "objective": {"type": "string"},
                "key_result": {"type": "string"},
                "target": {"type": "string"},
                "current": {"type": "string"},
                "confidence": {"type": "string"},
            },
            "required": ["objective", "key_result"],
        }},
        "errors": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["rows", "errors"],
}


def _scout(cfg: Config, agent: str, prompt: str, tools: tuple[str, ...], schema: dict) -> dict:
    # Scouts run in parallel threads, so each records its run on its own connection.
    prompt += " Put text in errors only when a tool call fails; an empty result is not an error."
    started = db.now_iso()
    result = claude.run(
        prompt, cwd=cfg.home, model=cfg.model("scout"), max_turns=6, allowed_tools=tools,
        json_schema=schema, settings=cfg.headless_settings, persist=False, timeout=240,
    )
    with closing(db.connect(cfg.db_path)) as conn:
        db.record_run(conn, agent=agent, model=cfg.model("scout"), started=started, result=result)
    data = result.structured if isinstance(result.structured, dict) else {}
    if not result.ok:
        data.setdefault("errors", []).append(f"{agent}: {result.error}")
    return data


def calendar(cfg: Config, day: date) -> dict:
    ids = cfg.section("calendar").get("ids", ["primary"])
    until = day + timedelta(days=2)
    prompt = (
        f"Fetch my calendar events from {day.isoformat()}T00:00:00 to {until.isoformat()}T00:00:00 local time "
        f"(today and tomorrow) using list_events on these calendars: {', '.join(ids)}. "
        "Return every event in the schema. Write start/end in my calendar's local time as YYYY-MM-DDTHH:MM "
        "(date only for all-day events). response is my own attendance status ('none' if I'm the only attendee). "
        "attendees counts all attendees; attendee_names lists up to 12 names (or emails). Call nothing except list_events."
    )
    return _scout(cfg, "scout-calendar", prompt, ("mcp__claude_ai_Google_Calendar__list_events",), CALENDAR_SCHEMA)


def jira(cfg: Config) -> dict:
    j = cfg.section("jira")
    if not j.get("cloud_id"):
        return {"issues": [], "errors": ["jira: cloud_id not set in me.toml"]}
    prompt = (
        f"Call searchJiraIssuesUsingJql with cloudId '{j['cloud_id']}', jql \"{j.get('jql_mine')}\", "
        "maxResults 30, fields summary,status,priority,issuetype,updated,duedate. Return each issue in the schema; "
        "status_category is the status category name (To Do / In Progress / Done). Call nothing else."
    )
    return _scout(cfg, "scout-jira", prompt, ("mcp__claude_ai_Atlassian__searchJiraIssuesUsingJql",), JIRA_SCHEMA)


def okr(cfg: Config) -> dict:
    file_id = cfg.section("sheets").get("okr_id")
    if not file_id:
        return {"rows": [], "errors": []}
    prompt = (
        f"Call read_file_content with fileId '{file_id}'. It's a goals sheet. Return each data row (skip the header "
        "and empty rows) in the schema. Call nothing else."
    )
    return _scout(cfg, "scout-okr", prompt, ("mcp__claude_ai_Google_Drive__read_file_content",), OKR_SCHEMA)


def parse_local(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt


JIRA_DETAIL_SCHEMA = {
    "type": "object",
    "properties": {
        "issues": {"type": "array", "items": {
            "type": "object",
            "properties": {
                **JIRA_SCHEMA["properties"]["issues"]["items"]["properties"],
                "has_acceptance_criteria": {"type": "boolean"},
                "days_since_update": {"type": "integer"},
            },
            "required": ["key", "summary", "status", "status_category", "has_acceptance_criteria"],
        }},
        "errors": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["issues", "errors"],
}


def jira_detail(cfg: Config, jql: str | None = None) -> dict:
    """Like jira(), plus whether each description states acceptance criteria."""
    j = cfg.section("jira")
    if not j.get("cloud_id"):
        return {"issues": [], "errors": ["jira: cloud_id not set in me.toml"]}
    prompt = (
        f"Call searchJiraIssuesUsingJql with cloudId '{j['cloud_id']}', jql \"{jql or j.get('jql_mine')}\", "
        "maxResults 30, fields summary,status,priority,issuetype,updated,duedate,description. Return each issue in the "
        "schema; has_acceptance_criteria is true when the description states testable acceptance criteria or a clear "
        f"definition of done. days_since_update is relative to {date.today().isoformat()}. Call nothing else."
    )
    return _scout(cfg, "scout-jira", prompt, ("mcp__claude_ai_Atlassian__searchJiraIssuesUsingJql",), JIRA_DETAIL_SCHEMA)
