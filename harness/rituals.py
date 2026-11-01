"""Plan, shutdown and diary as conversations inside the terminal UI.

Claude talks with the user through headless runs that resume one session per
ritual. It can look things up (vault notes, calendar, Jira) but changes nothing.
When the user agrees, Claude ends its reply with one fenced `me-<kind>` JSON
block. Python validates the block and turns it into a checklist: the private
note writes always happen, and each outward change (calendar, Jira, Sheets,
agent approvals) runs only if the user leaves it ticked. Outward changes go
through the proposals executor, so they keep its allow-lists and audit trail.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import date, datetime

from . import ask, claude, proposals, sheets, vault, wellbeing
from . import brief as briefmod
from . import day as daymod
from . import today as todaymod
from .config import Config

KINDS = ("plan", "shutdown", "diary")
TITLES = {"plan": "Plan", "shutdown": "Shutdown", "diary": "Diary"}
_BLOCK = re.compile(r"```me-(plan|shutdown|diary)[ \t]*\n(.*?)(?:```|\Z)", re.S)
_JIRA_KEY = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")
# Ritual turns may read; anything that writes or reaches the web stays off whatever settings allow.
_NEVER = ("Bash", "Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch")
_FORCE_APPROVE = {"defaults": {"level": "approve"}}  # the user ticked it, so it runs; never auto, never dropped
DIARY_OPENING = ("How was today? Write as much as you like, in as many messages as you like. "
                 "Say when you're done and I'll shape it into an entry for you to save.")
TRANSCRIPT_WORDS = 150  # keep the raw words by default once there are this many


# ---------------------------------------------------------------- what Claude is told

def persona(cfg: Config, kind: str, now: datetime) -> str:
    when = f"Today is {now:%A %d %B %Y}, now {now:%H:%M}."
    hands_off = ("You can look things up but you cannot change anything: the app makes changes only after the user "
                 "approves a checklist. Never say you created, moved, logged or saved something.")
    style = "Write plain text for a terminal: short paragraphs and '-' bullets, no tables, no headings."
    crisis = ("If the user mentions self-harm, suicidal thoughts or being unsafe, set everything else aside, respond "
              "with care and directness, encourage them to contact someone they trust right now, and share this: "
              f"{wellbeing.crisis_message(cfg)}")
    jira = cfg.section("jira")
    if kind == "plan":
        return "\n".join([
            f"You are {cfg.name}'s chief of staff, running the morning planning session inside the `me` terminal app. "
            "A short conversation, about five minutes: you bring a sharp proposal and the user decides. Direct, no filler.",
            f"{when} Work hours {cfg.section('schedule').get('work_hours', '09:00-18:30')}. Jira site {jira.get('site') or '-'}, "
            f"cloudId {jira.get('cloud_id') or '-'}.", hands_off, style,
            "1. First message: the shape of the day (meetings and hours, focus windows, anything all-day); a proposed "
            "Top 3, each with its why; also on the radar (reviews waiting, failing CI, Jira, carry-over, inbox todos, "
            "pending agent approvals), one line each; risks (more than 5h of meetings, Top 3 estimates longer than the "
            "focus windows, conflicts). Items with \"stale\": true are not candidates. End with: \"Energy today, 1–5? "
            "And does this Top 3 look right?\"",
            "2. Converge: if energy is 2 or less or meetings are over 5h, suggest Top 1–2 and say what you'd defer. "
            "Swap, reword and re-estimate as the user says. Fit focus blocks only inside focus windows, never over "
            "meetings. Stop once the user agrees; don't over-polish.",
            "3. When the user agrees, reply with one short line and end with exactly one block:\n"
            '```me-plan\n{"energy": 3, "top3": [{"title": "Fix flaky auth test", "ref": "PROJ-142", "est_min": 60}], '
            '"blocks": [{"start": "09:30", "end": "10:30", "task": "Fix flaky auth test"}], "also": ["Reply to Ravi"]}\n```\n'
            "top3 has 1–3 items; ref is a Jira key or URL, or left out. blocks are today, 24h HH:MM. energy is 1–5 or "
            "left out. The app turns the block into a checklist for the user (save the note, focus blocks on the "
            "calendar, Top 3 tickets in To Do moved to In Progress, the Work Log row, pending agent approvals). If "
            "they want changes after that, talk it through and send a new block.",
            crisis])
    if kind == "shutdown":
        return "\n".join([
            f"You are {cfg.name}'s chief of staff, running the end-of-day shutdown inside the `me` terminal app. The goal "
            "is to let them stop thinking about work tonight. Quick, honest and kind; never grade them.",
            f"{when} Jira cloudId {jira.get('cloud_id') or '-'}.", hands_off, style,
            "1. First message: the Top 3 and what's ticked. Where evidence suggests an unticked item is done (a matching "
            "merged PR, a ticket moved to Done), ask: \"Looks like X is done (PR #42 merged). Mark it?\" Then time, one "
            "line each: meetings, Claude Code by project, commits / PRs / reviews, Jira moves. For Jira moves, search "
            "with JQL `assignee = currentUser() AND status CHANGED DURING (startOfDay(), now())`, fields summary,status; "
            "skip it if that fails. Ask together: \"What's done? What carries to tomorrow? What's the first thing "
            "tomorrow? Any win worth keeping?\" If a Top 3 item had a Jira ticket and focus time, offer a worklog in one "
            "line (\"Log 1h on PROJ-142?\").",
            "2. Once you have their answers, reply with one sincere, specific line about the day and end with exactly "
            "one block:\n"
            '```me-shutdown\n{"done": ["Fix flaky auth test"], "carry": ["Write migration plan"], '
            '"tomorrow_first": "Write migration plan", "wins": ["Unblocked Ravi\'s release"], "jira_moved": 2, '
            '"note": "", "worklogs": [{"key": "PROJ-142", "minutes": 60}]}\n```\n'
            "done titles must match the plan's Top 3 or Also titles exactly. worklogs only for tickets the user said yes "
            "to, else []. The app turns the block into a checklist (save the shutdown and carry-over, the Work Log row, "
            "worklogs).",
            "If it's after 20:00, or Claude Code time was over 8h, gently suggest stopping for the night. Say it once.",
            crisis])
    return "\n".join([
        f"You are a warm, brief companion helping {cfg.name} write their private diary inside the `me` terminal app. "
        "You are not a therapist and you never diagnose. The entry belongs to them: keep their voice and don't moralise. "
        "Use no tools.", when, style,
        "- Let them write. Ask at most one follow-up, and only if it would help: an open loop they mentioned, or a feeling "
        "they named without context.",
        "- If they're stuck on a negative thought, you may offer one gentle reframing step: thought → evidence for and "
        "against → a kinder, realistic alternative. Offer it, don't push it.",
        "- When they say they're done, shape the entry in their words and end your reply with exactly one block:\n"
        '```me-diary\n{"entry": "**Mood** 3/5 · **Energy** 2/5\\n\\n**Highlights**: …\\n**Hard parts**: …\\n'
        '**Lessons**: …\\n**Grateful for**: …\\n**Open loops**:\\n- [ ] …"}\n```\n'
        "Leave out any heading with nothing under it. Include Mood and Energy only if stated or clearly implied; never "
        "invent them. Don't repeat the entry in your message, because the app shows it for them to save; your message is "
        "one sincere, specific acknowledgement, no pep talk.",
        "- Diary text stays private: it is saved only to their private vault, never anywhere else.",
        crisis + " Send a block only if they still want the entry saved."])


def gather(cfg: Config, conn, kind: str, now: datetime) -> dict:
    """What Claude needs to open the ritual. Plan rebuilds a stale brief first (up to a minute)."""
    day = now.date()
    if kind == "plan":
        t = todaymod.load(cfg, day)
        errors = []
        if todaymod.is_stale(t, now):
            try:
                t = briefmod.run(cfg, conn, day, now)
            except Exception as e:  # noqa: BLE001 - plan from what we have; Claude can still look things up
                errors.append(f"brief: {e}")
        checks = [{k: c[k] for k in ("ts", "mood", "energy", "stress")} for c in wellbeing.checkins(conn, day)]
        pending = [{k: p[k] for k in ("id", "agent", "action", "preview", "safe")} for p in proposals.listing(conn, "pending")]
        return {"today": t, "checkins_today": checks, "pending_approvals": pending[:8], "errors": errors}
    if kind == "shutdown":
        t = todaymod.load(cfg, day)
        return {"actuals": daymod.actuals(cfg, day, t), "jira": (t or {}).get("jira", [])}
    return {}


def opening(kind: str, context: dict) -> str:
    if kind == "plan":
        lead = "Start the planning session. Today's data from the brief, today's check-ins and pending agent approvals:"
    elif kind == "shutdown":
        lead = ("Start the shutdown. Today's actuals: the plan's Top 3 and Also items with their checkbox state, GitHub "
                "commits, merged PRs and reviews, Claude Code minutes per project, meeting minutes, and my open Jira issues:")
    else:
        return ""
    return f"{lead}\n\nDATA\n{json.dumps(context, ensure_ascii=False, default=str)}"


def turn(cfg: Config, kind: str, prompt: str, session: str | None, emit, cancel=None) -> claude.RunResult:
    common = dict(cwd=cfg.home, model=cfg.model("interactive"), append_system_prompt=persona(cfg, kind, datetime.now()),
                  resume=session, settings=cfg.headless_settings, disallowed_tools=_NEVER, on_event=emit, timeout=600,
                  cancel=cancel)
    if kind == "diary":
        return claude.run(prompt, max_turns=3, mcp=False, **common)
    return claude.run(prompt, max_turns=25, allowed_tools=ask.READ_TOOLS,
                      add_dirs=[z.vault for z in cfg.zones.values()], **common)


# ---------------------------------------------------------------- what comes back

def validate_diary(data: dict) -> dict:
    entry = data.get("entry")
    if not isinstance(entry, str) or not entry.strip():
        raise ValueError("diary needs a non-empty entry")
    return {"entry": entry.strip()}


VALIDATE = {"plan": daymod.validate_plan, "shutdown": daymod.validate_shutdown, "diary": validate_diary}


def extract(kind: str, text: str) -> tuple[str, dict | None, str | None]:
    """Split a reply into (text to show, validated block data, error). Blocks for other rituals are dropped."""
    found: list[str] = []

    def cut(m: re.Match) -> str:
        if m.group(1) == kind:
            found.append(m.group(2))
        return ""

    shown = re.sub(r"\n{3,}", "\n\n", _BLOCK.sub(cut, text)).strip()
    if not found:
        return shown, None, None
    try:
        data = json.loads(found[-1])
        if not isinstance(data, dict):
            raise ValueError("expected a JSON object")
        return shown, VALIDATE[kind](data), None
    except ValueError as e:  # bad JSON (JSONDecodeError is a ValueError) or a failed validation
        return shown, None, str(e)


# ---------------------------------------------------------------- the checklist

def _item(id_: str, label: str, detail: str = "", *, fixed: bool = False, checked: bool = True, **spec) -> dict:
    return {"id": id_, "label": label, "detail": detail, "fixed": fixed, "checked": checked or fixed,
            "status": "", "result": "", **spec}


def _hours(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return " ".join(part for part in (f"{h}h" if h else "", f"{m}m" if m else "") if part) or "0m"


def checklist(cfg: Config, kind: str, data: dict, context: dict, messages: list[dict], day: date | None = None) -> list[dict]:
    """Private writes first (fixed), outward changes after (ticked by default unless noted), Sheets last."""
    day = day or date.today()
    jira = cfg.section("jira")
    cloud, site = jira.get("cloud_id"), jira.get("site")
    items: list[dict] = []
    if kind == "plan":
        today = context.get("today") or {}
        items.append(_item("plan", "Save the plan to today's note", "Top 3: " + "; ".join(t["title"] for t in data["top3"]),
                           fixed=True))
        booked = {e["start"] for e in today.get("events", []) if e["title"].startswith(daymod.FOCUS_PREFIX)}
        for b in data.get("blocks") or []:
            have = b["start"] in booked
            items.append(_item(
                f"focus-{b['start']}", f"Calendar: {daymod.FOCUS_PREFIX} {b['start']}–{b['end']} {b['task']}",
                "already on your calendar" if have else "a focus block on your primary calendar", checked=not have,
                agent="plan", action="focus_block", target=f"{day} {b['start']}",
                payload={"executor": "tool", "tools": ["mcp__claude_ai_Google_Calendar__create_event"],
                         "instruction": "Create this event on the primary calendar, with no attendees.",
                         "input": {"summary": f"{daymod.FOCUS_PREFIX} {b['task']}", "startTime": f"{day}T{b['start']}:00",
                                   "endTime": f"{day}T{b['end']}:00", "description": "Focus block · me_harness"}},
                undo="Delete the focus event"))
        issues = {j["key"]: j for j in today.get("jira", [])}
        for t in data["top3"]:
            key = t.get("ref") or ""
            if cloud and _JIRA_KEY.match(key) and issues.get(key, {}).get("status_category") == "To Do":
                items.append(_item(
                    f"jira-{key}", f"Jira: move {key} to In Progress", issues[key].get("summary", ""),
                    agent="plan", action="transition", target=key,
                    payload={"executor": "tool", "tools": ["mcp__claude_ai_Atlassian__getTransitionsForJiraIssue",
                                                           "mcp__claude_ai_Atlassian__transitionJiraIssue"],
                             "instruction": f"Move Jira issue {key} (cloudId {cloud}) to In Progress: get its transitions, "
                                            "pick the one whose target status category is In Progress, and apply it.",
                             "input": {"issueKey": key, "cloudId": cloud}},
                    undo=f"Move {key} back on https://{site}/browse/{key}" if site else f"Move {key} back to To Do"))
        for p in context.get("pending_approvals") or []:
            items.append(_item(f"approve-{p['id']}", f"Approve {p['agent']}/{p['action']}: {p['preview'].splitlines()[0][:70]}",
                               "waiting from the agents; tick to approve it now", checked=False, approve=p["id"]))
    elif kind == "shutdown":
        carry = data.get("carry") or []
        detail = f"{len(data.get('done') or [])} done, {len(carry)} carried to tomorrow"
        if data.get("tomorrow_first"):
            detail += f", starting with {data['tomorrow_first']}"
        items.append(_item("shutdown", "Save the shutdown to today's note", detail, fixed=True))
        for w in data.get("worklogs") or []:
            if not cloud:
                break
            items.append(_item(
                f"worklog-{w['key']}", f"Jira: log {_hours(w['minutes'])} on {w['key']}", w.get("comment", ""),
                agent="shutdown", action="worklog", target=w["key"],
                payload={"executor": "tool", "tools": ["mcp__claude_ai_Atlassian__addWorklogToJiraIssue"],
                         "instruction": f"Add a worklog to Jira issue {w['key']} (cloudId {cloud}) for today.",
                         "input": {"issueKey": w["key"], "cloudId": cloud, "timeSpent": _hours(w["minutes"]),
                                   "comment": w.get("comment") or "Logged at shutdown · me_harness"}},
                undo=f"Delete the worklog on {w['key']}"))
    else:
        path = vault.note_path(cfg, "diary", day)
        items.append(_item("entry", f"Save the entry to {path.parent.name}/{path.name}", "your private vault only", fixed=True))
        said = [m["text"] for m in messages if m["role"] == "you"]
        words = sum(len(s.split()) for s in said)
        items.append(_item("transcript", "Also keep your own words, word for word", f"{words} words",
                           checked=words >= TRANSCRIPT_WORDS))
    if kind in ("plan", "shutdown") and cfg.section("sheets").get("worklog_id"):
        items.append(_item("sheets", "Sheets: update today's Work Log row",
                           "planned columns" if kind == "plan" else "actual columns", sheets=True))
    return items


def apply(cfg: Config, conn, kind: str, data: dict, items: list[dict], context: dict, messages: list[dict],
          now: datetime | None = None, on_change=lambda: None) -> None:
    """Do the fixed items and the ticked ones, in order, recording each outcome on its item.
    If a private write fails, nothing outward runs."""
    now = now or datetime.now()
    stamp = now.isoformat(timespec="seconds")
    blocked = None
    for item in items:
        if blocked or not item["checked"]:
            item["status"], item["result"] = "skipped", blocked or "not ticked"
            continue
        item["status"] = "running"
        on_change()
        try:
            status, result = _do(cfg, conn, kind, data, item, context, messages, now, stamp)
        except Exception as e:  # noqa: BLE001 - recorded on the item and shown
            status, result = "failed", str(e)[:300]
        item["status"], item["result"] = status, result
        if item["fixed"] and status == "failed":
            blocked = "not run: saving failed"
        on_change()


def _do(cfg, conn, kind, data, item, context, messages, now, stamp) -> tuple[str, str]:
    if item["id"] == "plan":
        path = daymod.commit_plan(cfg, conn, now.date(), data, now)
        return "done", f"saved to {path.name}"
    if item["id"] == "shutdown":
        written = daymod.commit_shutdown(cfg, conn, now.date(), data, context.get("actuals") or {}, now)
        return "done", "saved to " + ", ".join(p.name for p in written)
    if item["id"] == "entry":
        path = vault.write_section(cfg, "diary", f"entry-{now:%H%M}", f"### {now:%H:%M}\n{data['entry']}", now.date())
        return "done", f"saved to {path.name}"
    if item["id"] == "transcript":
        said = "\n\n".join(m["text"] for m in messages if m["role"] == "you")
        quoted = "\n".join("> " + line if line else ">" for line in said.splitlines())
        vault.write_section(cfg, "diary", f"transcript-{now:%H%M}", f"> [!quote]- Transcript\n{quoted}", now.date())
        return "done", "kept"
    if item.get("sheets"):
        out = sheets.sync(cfg, conn)
        return ("done" if out["status"] in ("ok", "skipped") else "warn"), out["detail"]
    if item.get("approve"):
        status, result = proposals.approve(cfg, conn, item["approve"])
        return ("done" if status == "executed" else "failed"), (result or status)[:200]
    pid, outcome = proposals.propose(cfg, conn, _FORCE_APPROVE, agent=item["agent"], action=item["action"],
                                     target=item["target"], payload=item["payload"], preview=item["label"],
                                     reason=f"approved in the {kind} session", undo=item.get("undo", ""), key=stamp)
    if outcome != "pending":
        return "failed", f"not queued: {outcome}"
    status, result = proposals.approve(cfg, conn, pid)
    return ("done" if status == "executed" else "failed"), (result or status)[:200]


# ---------------------------------------------------------------- one session

class Session:
    """One ritual conversation. The UI calls open/reply/apply on a worker thread (each with that
    thread's own db connection) and reads messages, stage and items to draw."""

    def __init__(self, cfg: Config, kind: str, label=lambda block: block.get("name", "")):
        if kind not in KINDS:
            raise ValueError(f"ritual must be one of {', '.join(KINDS)}")
        self.cfg, self.kind, self.label = cfg, kind, label
        self.messages: list[dict] = []
        self.session_id: str | None = None
        self.context: dict = {}
        self.data: dict | None = None
        self.items: list[dict] = []
        self.stage = "gathering"  # gathering → talk ⇄ review → applying → done
        self.cancel = threading.Event()

    @property
    def title(self) -> str:
        return TITLES[self.kind]

    def open(self, conn, now: datetime | None = None) -> None:
        if self.kind == "diary":
            self.messages.append({"role": "me", "text": DIARY_OPENING, "status": "done"})
            self.stage = "talk"
            return
        try:
            self.context = gather(self.cfg, conn, self.kind, now or datetime.now())
        except Exception as e:  # noqa: BLE001 - talk anyway; Claude can look things up
            self.context = {"errors": [str(e)]}
        self.stage = "talk"
        self._exchange(opening(self.kind, self.context))

    def reply(self, text: str) -> None:
        self.messages.append({"role": "you", "text": text})
        if wellbeing.CRISIS.search(text):
            self.messages.append({"role": "note", "text": wellbeing.crisis_message(self.cfg)})
        self.data, self.items, self.stage = None, [], "talk"
        if self.session_id:
            prompt = text
        else:  # the first turn, or every earlier one failed: send everything so far
            said = "\n\n".join(m["text"] for m in self.messages if m["role"] == "you")
            prompt = f"{opening(self.kind, self.context)}\n\nThe user says:\n{said}".strip() if self.kind != "diary" else said
        self._exchange(prompt)

    def toggle(self, index: int) -> None:
        if self.stage == "review" and 0 <= index < len(self.items) and not self.items[index]["fixed"]:
            self.items[index]["checked"] = not self.items[index]["checked"]

    def apply(self, conn, on_change=lambda: None) -> None:
        self.stage = "applying"
        try:
            apply(self.cfg, conn, self.kind, self.data, self.items, self.context, self.messages, on_change=on_change)
        finally:
            self.stage = "done"

    def outcome(self) -> str:
        bad = sum(1 for i in self.items if i["status"] in ("failed", "warn"))
        return f"{self.kind} saved" + (f"; {bad} item{' needs' if bad == 1 else 's need'} a look" if bad else "")

    def _exchange(self, prompt: str) -> None:
        msg = {"role": "me", "text": "", "tools": [], "status": "thinking"}
        self.messages.append(msg)
        raw: list[str] = []

        def emit(event: dict) -> None:
            if event.get("type") != "assistant":
                return
            for block in (event.get("message") or {}).get("content") or []:
                if block.get("type") == "text" and block.get("text", "").strip():
                    raw.append(block["text"])
                    shown = extract(self.kind, block["text"])[0]
                    if shown:
                        msg["text"] += ("\n\n" if msg["text"] else "") + shown
                elif block.get("type") == "tool_use":
                    msg["tools"].append(self.label(block))

        try:  # a stop pressed while gathering counts for this turn too; it's cleared once the turn ends
            r = (claude.RunResult(error="cancelled") if self.cancel.is_set() else
                 turn(self.cfg, self.kind, prompt, self.session_id, emit, self.cancel))
        except Exception as e:  # noqa: BLE001 - shown in the transcript
            r = claude.RunResult(error=str(e))
        finally:
            self.cancel.clear()
        shown, data, error = extract(self.kind, "\n\n".join(raw) or r.text)
        msg["text"] = shown
        if r.ok:
            self.session_id = r.session_id or self.session_id
            msg["cost"] = r.cost_usd
        else:
            msg["error"] = r.error or "no answer"
        if error:
            self.messages.append({"role": "note", "text": f"That {self.kind} didn't come through cleanly ({error}). "
                                                          "Say \"try again\" and I'll resend it."})
        elif data is not None:
            self.data, self.items, self.stage = data, checklist(self.cfg, self.kind, data, self.context, self.messages), "review"
        msg["status"] = "done"
