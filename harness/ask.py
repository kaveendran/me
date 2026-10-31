"""`me ask`: one question to the chief of staff, streamed or as structured JSON.

Used by the Obsidian sidebar plugin and Mission Control's command bar. It's read-only:
the vaults (including the memory wiki) plus read-only Calendar and Jira lookups.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import claude
from .config import Config

READ_TOOLS = ("Read", "Glob", "Grep",
              "mcp__claude_ai_Google_Calendar__list_events", "mcp__claude_ai_Google_Calendar__search_events",
              "mcp__claude_ai_Atlassian__searchJiraIssuesUsingJql", "mcp__claude_ai_Atlassian__getJiraIssue")

MODES = {
    "links": ({"type": "object", "properties": {
                  "links": {"type": "array", "items": {"type": "object", "properties": {
                      "phrase": {"type": "string"}, "target": {"type": "string"}}, "required": ["phrase", "target"]}},
                  "tags": {"type": "array", "items": {"type": "string"}}},
               "required": ["links", "tags"]},
              "Suggest up to 8 wikilinks for the note below: each phrase must appear verbatim in the note (not already "
              "inside [[ ]]) and each target must be one of these existing note titles: {titles}. Suggest up to 5 tags "
              "as #lowercase. Use no tools.\n\nNOTE\n{note}"),
    "tasks": ({"type": "object", "properties": {"tasks": {"type": "array", "items": {"type": "string"}}},
               "required": ["tasks"]},
              "List the concrete action items in the note below, each as a short imperative task with names and dates "
              "kept exactly. Skip anything already ticked [x]. Use no tools.\n\nNOTE\n{note}"),
    "summary": ({"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]},
                "Summarise the note below in 3-5 plain sentences for a personal diary. Use no tools.\n\nNOTE\n{note}"),
}


def persona(cfg: Config, note: Path | None) -> str:
    zones = ", ".join(f"{z.name} vault {z.vault}{' (PUBLIC repo)' if z.public else ''}" for z in cfg.zones.values())
    private = cfg.zone_for("daily").vault
    lines = [
        "You are kaveendran's chief of staff (me_harness). Be concise and specific; cite note paths you used.",
        f"Vaults: {zones}. The memory wiki is {private}/openwiki (start at quickstart.md when it exists).",
        f"Jira cloudId {cfg.section('jira').get('cloud_id')}. You are read-only here: never write files.",
    ]
    if note:
        lines.append(f"The user is looking at the note {note}; read it when the question is about 'this note'.")
    return " ".join(lines)


def stream(cfg: Config, question: str, note: Path | None, session: str | None, emit) -> claude.RunResult:
    dirs = [z.vault for z in cfg.zones.values()]
    return claude.run(question, cwd=cfg.home, model=cfg.model("headless"), max_turns=20, allowed_tools=READ_TOOLS,
                      append_system_prompt=persona(cfg, note), resume=session, add_dirs=dirs,
                      settings=cfg.headless_settings, on_event=emit, timeout=600)


def note_titles(cfg: Config, limit: int = 1500) -> list[str]:
    titles = set()
    for zone in cfg.zones.values():
        for p in zone.vault.rglob("*.md"):
            if not any(part.startswith(".") or part in ("_templates",) for part in p.relative_to(zone.vault).parts):
                titles.add(p.stem)
    return sorted(titles)[:limit]


def structured(cfg: Config, mode: str, note: Path) -> dict:
    schema, template = MODES[mode]
    text = note.read_text(encoding="utf-8")
    titles = note_titles(cfg) if mode == "links" else []
    prompt = template.format(note=text[:20000], titles=json.dumps(titles))
    r = claude.run(prompt, cwd=cfg.home, model=cfg.model("scout" if mode != "links" else "headless"), max_turns=2,
                   json_schema=schema, settings=cfg.headless_settings, persist=False, mcp=False, timeout=240)
    if not r.ok or not isinstance(r.structured, dict):
        raise RuntimeError(r.error or "no structured result")
    out = r.structured
    if mode == "links":
        valid = set(titles)
        linked = set(re.findall(r"\[\[([^\]|#]+)", text))
        out["links"] = [l for l in out["links"] if l["target"] in valid and l["phrase"] in text and l["target"] not in linked
                        and l["target"] != note.stem]
        out["tags"] = [t if t.startswith("#") else f"#{t}" for t in out["tags"]]
    return out
