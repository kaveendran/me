"""Autonomous agents: GitHub triage, Jira triage, calendar guardian, meeting prep, inbox librarian.

Every agent follows one shape: gather deterministically, ask the model for judgment
(structured, no tools, no MCP), then turn each judgment into a proposal. Whether a
proposal runs on its own or waits for approval is autonomy.toml's call, not the agent's.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from . import claude, db, github, notify, proposals, scouts, vault
from . import day as daymod
from . import today as todaymod
from .config import Config

_KEY = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")


def judge(cfg: Config, conn, agent: str, prompt: str, schema: dict, model: str = "headless") -> dict:
    started = db.now_iso()
    r = claude.run(prompt, cwd=cfg.home, model=cfg.model(model), max_turns=2, json_schema=schema,
                   settings=cfg.headless_settings, persist=False, mcp=False, timeout=300)
    db.record_run(conn, agent=agent, model=cfg.model(model), started=started, result=r)
    if not r.ok:
        raise RuntimeError(f"{agent} judgment failed: {r.error}")
    return r.structured if isinstance(r.structured, dict) else {}


def obsidian_url(cfg: Config, path: Path) -> str:
    zone = vault.zone_of(cfg, path)
    rel = path.resolve().relative_to(zone.vault.resolve()) if zone else path
    return f"obsidian://open?vault={quote(zone.vault.name if zone else '')}&file={quote(str(rel))}"


# ---------------------------------------------------------------- vault ops

def _op_append_triage(cfg: Config, payload: dict) -> str:
    path = vault.ensure_note(cfg, "daily")
    current = vault.read_section(path.read_text(encoding="utf-8"), "triage") or ""
    entry = payload["markdown"].strip()
    if entry not in current:
        vault.write_section(cfg, "daily", "triage", (current + "\n" + entry).strip())
    return f"noted in {path.name}"


def _op_write_note(cfg: Config, payload: dict) -> str:
    path = Path(payload["path"])
    if vault.private_target_reason(cfg, path, payload["text"]):
        raise ValueError("refusing to write private content into a public vault")
    if not path.exists() or payload.get("overwrite"):
        vault.atomic_write(path, payload["text"])
    return f"wrote {path.name}"


def _op_promote(cfg: Config, payload: dict) -> str:
    src, dest = Path(payload["src"]), Path(payload["dest"])
    text = re.sub(r"(?m)^private: true$", "private: false", src.read_text(encoding="utf-8"))
    if reason := vault.private_target_reason(cfg, dest, text):
        raise ValueError(reason)
    if dest.exists():
        raise ValueError(f"{dest} already exists")
    vault.atomic_write(dest, text)
    return f"promoted to {dest}"


proposals.VAULT_OPS.update(append_triage=_op_append_triage, write_note=_op_write_note, promote=_op_promote)


# ---------------------------------------------------------------- GitHub

GH_SCHEMA = {
    "type": "object",
    "properties": {
        "ci": {"type": "array", "items": {"type": "object", "properties": {
            "pr_url": {"type": "string"}, "summary": {"type": "string"}, "likely_cause": {"type": "string"},
            "suggested_fix": {"type": "string"}, "confidence": {"type": "string", "enum": ["low", "medium", "high"]}},
            "required": ["pr_url", "summary", "likely_cause", "suggested_fix", "confidence"]}},
        "reviews": {"type": "array", "items": {"type": "object", "properties": {
            "pr_url": {"type": "string"}, "verdict": {"type": "string", "enum": ["comment", "approve", "request_changes"]},
            "body": {"type": "string"}}, "required": ["pr_url", "verdict", "body"]}},
        "noise_thread_ids": {"type": "array", "items": {"type": "string"}},
        "labels": {"type": "array", "items": {"type": "object", "properties": {
            "issue_url": {"type": "string"}, "labels": {"type": "array", "items": {"type": "string"}}},
            "required": ["issue_url", "labels"]}},
    },
    "required": ["ci", "reviews", "noise_thread_ids", "labels"],
}

GH_PROMPT = """You triage GitHub for kaveendran. Judge only from the data below.

- ci: for each failing PR, read the failed log and give a one-line summary, the most likely cause, and a concrete
  suggested fix. Be honest about confidence.
- reviews: for each PR awaiting their review, draft a review they could post as-is: a two-line summary of the
  change, then specific findings (correctness, edge cases, naming), each with a file reference. verdict "approve"
  only if you found nothing that needs changing; "request_changes" only for real bugs; otherwise "comment".
- noise_thread_ids: notification threads that need no attention (bot or CI chatter, merged/closed things they
  only subscribed to, releases). Never include review requests, mentions or assignments.
- labels: for unlabeled issues, pick 1-2 labels from that repo's existing labels only (given per repo).
Leave any list empty when there's nothing to do. Use no tools.

DATA
{data}"""


def triage_github(cfg: Config, conn, policy: dict) -> dict:
    g = cfg.section("github")
    user, repos = g.get("user", ""), g.get("repos", [])
    snap = github.snapshot(repos)
    data: dict = {"failing_prs": [], "review_requests": [], "notifications": [], "unlabeled_issues": [], "repo_labels": {}}
    seen: dict[str, str] = {}
    for pr in [p for p in snap["my_prs"] if p.get("ci") == "failing"][:3]:
        info = github.gh_json("pr", "view", pr["url"], "--json", "headRefName,headRefOid")
        if db.get_cursor(conn, f"ci.{pr['url']}") == info["headRefOid"]:
            continue
        data["failing_prs"].append({"url": pr["url"], "title": pr["title"], "log": github.failed_log(pr["repo"], info["headRefName"])})
        seen[f"ci.{pr['url']}"] = info["headRefOid"]
    for r in [r for r in snap["reviews"] if not r["stale"]][:3]:
        if db.get_cursor(conn, f"review.{r['url']}") == r["updated"]:
            continue
        data["review_requests"].append({"url": r["url"], "title": r["title"], "author": r["author"], "diff": github.pr_diff(r["url"])})
        seen[f"review.{r['url']}"] = r["updated"]
    try:
        data["notifications"] = github.notifications()
        issues = github.unlabeled_own_issues(user) if user else []
        data["unlabeled_issues"] = [{"url": i["url"], "title": i["title"], "repo": i["repo"]} for i in issues[:8]]
        data["repo_labels"] = {repo: github.repo_labels(repo) for repo in {i["repo"] for i in issues[:8]}}
    except github.GhError as e:
        snap["errors"].append(str(e))
    if not any(data[k] for k in ("failing_prs", "review_requests", "notifications", "unlabeled_issues")):
        return {"agent": "github", "proposals": 0, "detail": "nothing new"}

    out = judge(cfg, conn, "triage-github", GH_PROMPT.format(data=json.dumps(data, indent=1)), GH_SCHEMA)
    made = []
    for d in out.get("ci", []):
        md = (f"- **CI failing** [{d['pr_url'].split('github.com/')[-1]}]({d['pr_url']}): {d['summary']}. "
              f"Likely: {d['likely_cause']}. Fix: {d['suggested_fix']} ({d['confidence']} confidence)")
        made.append(proposals.propose(cfg, conn, policy, agent="github", action="diagnose_ci_failure", target=d["pr_url"],
                                      key=seen.get(f"ci.{d['pr_url']}", ""), payload={"executor": "vault", "op": "append_triage",
                                      "markdown": md, **d}, preview=md, reason="CI is failing on your PR"))
        notify.send(cfg, conn, "ci", "CI failing", f"{d['pr_url'].split('github.com/')[-1]}: {d['likely_cause']}"[:200],
                    priority=4, tags=["x"], click=d["pr_url"])
    for rv in out.get("reviews", []):
        flag = {"approve": "--approve", "request_changes": "--request-changes"}.get(rv["verdict"], "--comment")
        made.append(proposals.propose(cfg, conn, policy, agent="github", action="review_comment", target=rv["pr_url"],
                                      key=seen.get(f"review.{rv['pr_url']}", ""),
                                      payload={"executor": "gh", "calls": [["pr", "review", rv["pr_url"], flag, "--body", rv["body"]]]},
                                      preview=f"{rv['verdict']} on {rv['pr_url']}:\n\n{rv['body']}",
                                      reason="Your review was requested", undo="Dismiss or edit the review on GitHub"))
    noise = [t for t in out.get("noise_thread_ids", []) if t.isdigit()]
    if noise:
        made.append(proposals.propose(cfg, conn, policy, agent="github", action="mark_notifications_read",
                                      target="notifications", key=",".join(sorted(noise)),
                                      payload={"executor": "gh", "calls": [["api", "-X", "PATCH", f"notifications/threads/{t}"] for t in noise]},
                                      preview=f"Mark {len(noise)} noise notifications as read", reason="No action needed"))
    for lb in out.get("labels", []):
        repo = "/".join(lb["issue_url"].split("github.com/")[-1].split("/")[:2])
        labels = [l for l in lb["labels"] if l in data["repo_labels"].get(repo, [])]
        if labels:
            made.append(proposals.propose(cfg, conn, policy, agent="github", action="label_own_repo_issues", target=lb["issue_url"],
                                          payload={"executor": "gh", "calls": [["issue", "edit", lb["issue_url"], "--add-label", ",".join(labels)]]},
                                          preview=f"Label {lb['issue_url']}: {', '.join(labels)}", reason="Unlabeled issue in your repo"))
    for key, value in seen.items():
        db.set_cursor(conn, key, value)
    return {"agent": "github", "proposals": len(made), "outcomes": [o for _, o in made]}


# ---------------------------------------------------------------- Jira

def triage_jira(cfg: Config, conn, policy: dict) -> dict:
    j = cfg.section("jira")
    site, cloud = j.get("site"), j.get("cloud_id")
    detail = scouts.jira_detail(cfg)
    issues = {i["key"]: i for i in detail.get("issues", [])}
    made = []
    try:
        merged = github.merged_since(date.today() - timedelta(days=2))
    except github.GhError:
        merged = []
    for pr in merged:
        for key in set(_KEY.findall(pr["title"])):
            issue = issues.get(key)
            if issue and issue["status_category"] != "Done":
                made.append(proposals.propose(
                    cfg, conn, policy, agent="jira", action="transition", target=key, key="done",
                    payload={"executor": "tool", "tools": ["mcp__claude_ai_Atlassian__getTransitionsForJiraIssue",
                                                           "mcp__claude_ai_Atlassian__transitionJiraIssue"],
                             "instruction": f"Move Jira issue {key} (cloudId {cloud}) to its Done status: get its transitions, "
                                            "pick the one whose target status category is Done, and apply it.",
                             "input": {"issueKey": key, "cloudId": cloud}},
                    preview=f"Move {key} \"{issue['summary']}\" → Done", reason=f"{pr['repo']}#{pr['number']} merged",
                    undo=f"Move {key} back on https://{site}/browse/{key}"))
    for key, issue in issues.items():
        if issue["status_category"] == "Done" or issue.get("has_acceptance_criteria", True):
            continue
        body = ("Could we add acceptance criteria to this ticket? A short list of testable outcomes would help me "
                "scope it and know when it's done. Thanks!")
        made.append(proposals.propose(
            cfg, conn, policy, agent="jira", action="comment", target=key, key="ac",
            payload={"executor": "tool", "tools": ["mcp__claude_ai_Atlassian__addCommentToJiraIssue"],
                     "instruction": f"Add this comment to Jira issue {key} (cloudId {cloud}).",
                     "input": {"issueKey": key, "cloudId": cloud, "body": body}},
            preview=f"Comment on {key} \"{issue['summary']}\":\n\n{body}", reason="No acceptance criteria in the description",
            undo="Delete the comment in Jira"))
    return {"agent": "jira", "proposals": len(made), "outcomes": [o for _, o in made], "errors": detail.get("errors", [])}


# ---------------------------------------------------------------- Calendar

def triage_calendar(cfg: Config, conn, policy: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    t = todaymod.load(cfg, now.date())
    if not t:
        return {"agent": "calendar", "proposals": 0, "detail": "no brief today"}
    day = now.date()
    events = [e for e in t["events"] if not e["title"].startswith(daymod.FOCUS_PREFIX)]
    made = []
    for a, b in zip(events, events[1:]):
        if b["start"] < a["end"] and a.get("response") == b.get("response") == "accepted":
            notify.send(cfg, conn, "conflict", "Calendar conflict", f"{a['start']} {a['title']} overlaps {b['title']}",
                        once_per_day=True)
    if t["meeting_min"] >= 300:
        notify.send(cfg, conn, "overload", "Heavy meeting day", f"{t['meeting_min'] / 60:.1f}h of meetings today",
                    once_per_day=True)
        for e in events:
            if e.get("response") in ("tentative", "needsAction") and not e.get("organizer_self") and e["attendees"] >= 6:
                made.append(proposals.propose(
                    cfg, conn, policy, agent="calendar", action="decline", target=e["id"], key=str(day),
                    payload={"executor": "tool", "tools": ["mcp__claude_ai_Google_Calendar__respond_to_event"],
                             "instruction": f"Decline the calendar event with id {e['id']} on the primary calendar.",
                             "input": {"eventId": e["id"], "responseStatus": "declined"}},
                    preview=f"Decline {e['start']} {e['title']} ({e['attendees']} people)",
                    reason=f"{t['meeting_min'] / 60:.1f}h of meetings and you haven't accepted this one",
                    undo="Accept it again in Google Calendar"))
    has_blocks = any(e["title"].startswith(daymod.FOCUS_PREFIX) for e in t["events"])
    windows = [w for w in daymod.focus_windows(daymod.timed_events(
        [{**e, "start": f"{day}T{e['start']}", "end": f"{day}T{e['end']}", "all_day": False} for e in t["events"]], day),
        daymod.work_bounds(cfg, day), now) if w["minutes"] >= 90]
    if t["meeting_min"] >= 240 and not has_blocks and windows:
        w = max(windows, key=lambda w: w["minutes"])
        made.append(proposals.propose(
            cfg, conn, policy, agent="calendar", action="focus_block", target=f"{day} {w['start']}", key=str(day),
            payload={"executor": "tool", "tools": ["mcp__claude_ai_Google_Calendar__create_event"],
                     "instruction": "Create this event on the primary calendar, with no attendees.",
                     "input": {"summary": f"{daymod.FOCUS_PREFIX} Focus (protected)", "startTime": f"{day}T{w['start']}:00",
                               "endTime": f"{day}T{w['end']}:00", "description": "Protected by me_harness on a heavy meeting day."}},
            preview=f"Protect {w['start']}–{w['end']} as focus time", reason=f"{t['meeting_min'] / 60:.1f}h of meetings today",
            undo="Delete the focus event"))
    return {"agent": "calendar", "proposals": len(made), "outcomes": [o for _, o in made]}


# ---------------------------------------------------------------- Meeting prep

def meeting_path(cfg: Config, day: date, start: str, title: str) -> Path:
    safe = re.sub(r"[\\/:*?\"<>|#^\[\]]+", " ", title).strip()[:60] or "Meeting"
    return cfg.zone_for("meetings" if "meetings" in cfg.routing else "daily").vault / "Meetings" / f"{day} {start.replace(':', '')} {safe}.md"


def _past_meetings(folder: Path, title: str, before: Path) -> list[Path]:
    words = {w.lower() for w in re.findall(r"\w{4,}", title)}
    found = [p for p in folder.glob("*.md") if p != before and words & {w.lower() for w in re.findall(r"\w{4,}", p.stem)}]
    return sorted(found, reverse=True)[:3]


def prep_meetings(cfg: Config, conn, policy: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    t = todaymod.load(cfg, now.date())
    if not t:
        return {"agent": "prep", "notes": 0}
    site = cfg.section("jira").get("site")
    made = 0
    for e in t["events"]:
        start = datetime.combine(now.date(), datetime.strptime(e["start"], "%H:%M").time())
        minutes = (start - now).total_seconds() / 60
        if not (5 <= minutes <= 25) or e["attendees"] < 2 or e["title"].startswith(daymod.FOCUS_PREFIX):
            continue
        path = meeting_path(cfg, now.date(), e["start"], e["title"])
        if path.exists():
            continue
        words = {w.lower() for w in re.findall(r"\w{4,}", e["title"])}
        jira = [j for j in t.get("jira", []) if words & {w.lower() for w in re.findall(r"\w{4,}", j["summary"])}][:5]
        past = _past_meetings(path.parent, e["title"], path)
        open_actions = []
        if past:
            open_actions = [a for a in daymod.unchecked(past[0].read_text(encoding="utf-8")) if a.strip()]
        lines = [f"**{e['start']}–{e['end']}** · {e['attendees']} people" + (f" · [join]({e['meet_url']})" if e.get("meet_url") else "")]
        if e.get("attendee_names"):
            lines.append("**Attendees**: " + ", ".join(e["attendee_names"][:12]))
        if jira:
            lines.append("**Related Jira**: " + ", ".join(daymod.ref_md(j["key"], site) + f" {j['summary']}" for j in jira))
        if past:
            lines.append("**Last time**: " + ", ".join(f"[[{p.stem}]]" for p in past))
        if open_actions:
            lines += ["**Open actions from last time**"] + [f"- [ ] {a}" for a in open_actions[:8]]
        lines += ["", "**Agenda**", "- "]
        template = (cfg.templates_dir / "meeting.md").read_text(encoding="utf-8")
        text = template.replace("{{date}}", str(now.date())).replace("{{title}}", e["title"])
        text = vault.replace_section(text, "prep", "\n".join(lines))
        _, outcome = proposals.propose(cfg, conn, policy, agent="vault", action="prep_note", target=str(path),
                                       payload={"executor": "vault", "op": "write_note", "path": str(path), "text": text},
                                       preview=f"Prep note for {e['start']} {e['title']}", reason="Meeting soon")
        if outcome == "executed":
            made += 1
            notify.send(cfg, conn, "prep", f"{e['title']} in {int(minutes)} min", "Prep note ready", tags=["spiral_notepad"],
                        click=obsidian_url(cfg, path))
    return {"agent": "prep", "notes": made}


# ---------------------------------------------------------------- Inbox librarian

CLIP_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
        "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        "related": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        "promote": {"type": "boolean"},
        "folder": {"type": "string"},
    },
    "required": ["summary", "tags", "related", "promote", "folder"],
}


def _titles(cfg: Config, limit: int = 400) -> list[str]:
    titles = []
    for zone in cfg.zones.values():
        for p in zone.vault.rglob("*.md"):
            if not any(part.startswith(".") or part in ("_templates", "openwiki") for part in p.relative_to(zone.vault).parts):
                titles.append(p.stem)
    return sorted(set(titles))[:limit]


def inbox_librarian(cfg: Config, conn, policy: dict) -> dict:
    private = cfg.zone_for("inbox").vault
    public = next((z.vault for z in cfg.zones.values() if z.public), None)
    clips = [p for p in sorted((private / "Inbox" / "clips").glob("*.md"))
             if vault.frontmatter(p.read_text(encoding="utf-8")).get("status") == "unprocessed"][:10]
    if not clips:
        return {"agent": "inbox", "processed": 0}
    titles = _titles(cfg)
    folders = sorted({p.name for p in public.iterdir() if p.is_dir() and not p.name.startswith(".")}) if public else []
    done = 0
    for clip in clips:
        text = clip.read_text(encoding="utf-8")
        prompt = (f"A web clip saved to a personal Obsidian vault is below. Summarise it in 2-4 bullets, suggest up to 5 "
                  f"lowercase tags, and pick up to 5 related notes ONLY from this list: {json.dumps(titles)}. Set promote "
                  f"to true only if it's general technical knowledge worth keeping in the public notes vault, and pick "
                  f"folder from: {json.dumps(folders)}. Use no tools.\n\nCLIP\n{text[:12000]}")
        out = judge(cfg, conn, "inbox", prompt, CLIP_SCHEMA, model="scout")
        related = [r for r in out.get("related", []) if r in titles]
        tags = [re.sub(r"[^a-z0-9/_-]", "", t.lower().lstrip("#")) for t in out.get("tags", [])]
        block = "\n".join([*(f"- {s}" for s in out.get("summary", [])), "",
                           "Related: " + ", ".join(f"[[{r}]]" for r in related) if related else "",
                           "Tags: " + " ".join(f"#{t}" for t in tags if t)]).strip()
        new = vault.replace_section(re.sub(r"(?m)^status: unprocessed$", "status: processed", text, count=1), "summary", block)
        proposals.propose(cfg, conn, policy, agent="vault", action="file_capture", target=str(clip),
                          payload={"executor": "vault", "op": "write_note", "path": str(clip), "text": new, "overwrite": True},
                          preview=f"Summarise and link {clip.name}", reason="New web clip")
        if out.get("promote") and public and out.get("folder") in folders:
            dest = public / out["folder"] / clip.name
            proposals.propose(cfg, conn, policy, agent="vault", action="promote_to_public", target=str(clip),
                              payload={"executor": "vault", "op": "promote", "src": str(clip), "dest": str(dest)},
                              preview=f"Copy {clip.name} to the public notes vault ({out['folder']}/)",
                              reason="Looks like general knowledge", undo=f"Delete {dest}")
        done += 1
    return {"agent": "inbox", "processed": done}
