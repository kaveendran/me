"""`me` with no arguments: the full-screen terminal UI (curses, stdlib only).

Tabs: Today · Approvals · Agents · Wellbeing · Setup · Chat · Plan. Data loads and
long work (brief, approvals, setup actions, Claude turns) run in background
threads, so the screen stays live. Only the main thread draws. Plan, shutdown and
diary are conversations in the Plan tab (`rituals.Session`). Anything that needs
the real terminal (sign-ins, the voice diary) suspends the UI and resumes it afterwards.

Rendering is split into pure functions (`render_*`) that turn data into lines of
(text, style) segments, so they can be tested without a terminal.
"""

from __future__ import annotations

import curses
import locale
import re
import shutil
import signal
import subprocess
import threading
import time
import textwrap
import unicodedata
from contextlib import closing
from datetime import datetime

from . import ask, db, host, proposals, rituals, setup, theme, vault, wellbeing
from . import brief as briefmod
from . import server
from . import today as todaymod
from .config import Config

TABS = ("Today", "Approvals", "Agents", "Wellbeing", "Setup", "Chat", "Plan")
CHAT, RITUAL = TABS.index("Chat"), TABS.index("Plan")
SPARK = "▁▂▃▄▅▆▇█"
ICONS = {"ok": "✓", "warn": "!", "todo": "○", "fail": "✗"}
STATUS_STYLE = {"ok": "ok", "warn": "warn", "todo": "accent", "fail": "bad", "error": "bad", "never": "dim"}
BAND_STYLE = {"ok": "ok", "elevated": "warn", "high": "bad"}
STATE_ICON = {"flow": "◆", "steady": "●", "busy": "▲", "strained": "◐", "rest": "☾"}
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
RITUALS = {"p": "plan", "s": "shutdown", "d": "diary"}
RITUAL_WAIT = {"plan": "Gathering your calendar, Jira and GitHub (up to a minute when the brief needs rebuilding)…",
               "shutdown": "Pulling today's commits, PRs, meetings and Claude Code time…"}
RITUAL_AFTER = {"plan": "Tick boxes in today's note as you go; the status line tracks it. x closes this.",
                "shutdown": "Press d to write your diary now, or x to close.", "diary": "x closes this."}
ITEM_MARK = {"done": ("✓", "ok"), "warn": ("!", "warn"), "failed": ("✗", "bad"), "skipped": ("–", "faint")}
CHAT_HINTS = ("What should I focus on next?", "What's waiting on me in Jira and GitHub?", "What did I get done this week?")
HELP = """Everywhere
  1-7, tab      switch tabs
  /             chat: ask anything, answered right here (read-only)
  ↑↓, j k       move the selection or scroll
  n  t  i       capture a note, todo or idea
  c             check in: mood, energy, stress
  p  s  d       plan the day, shut down, diary: a conversation in tab 7
  V             diary by voice (opens Claude Code)
  b             gather today's brief now
  T             appearance: theme, light or dark, adaptive colours
  r             refresh this tab
  q             quit

Approvals    ⏎ details   a approve   x reject   A approve every safe one
Agents       ⏎ run now   P pause or resume every scheduled job
Setup        ⏎ fix the selected item   e edit profile   D deep re-check
Chat         ⏎ or / ask   x stop the answer, or start a new chat   ↑↓ scroll
Plan         ⏎ or / reply   x stop the answer, or end   then the checklist:
             space tick or untick   ⏎ do the ticked ones   / change something

Plan, shutdown and diary never change anything by themselves. When you agree,
you get a checklist: your note is always saved, and focus blocks, Jira moves,
the Work Log and agent approvals happen only if you leave them ticked.

With adaptive colours on, the accent follows your day: emerald when you're in
flow, amber when it's busy, and soft and quiet when it's heavy or after hours."""

Line = list  # list[tuple[str, str]]: (text, style)


# ---------------------------------------------------------------- pure helpers

def sparkline(values: list, lo: float | None = None, hi: float | None = None) -> str:
    nums = [v for v in values if v is not None]
    if not nums:
        return "·" * len(values)
    lo = min(nums) if lo is None else lo
    hi = max(nums) if hi is None else hi
    span = (hi - lo) or 1
    return "".join("·" if v is None else SPARK[min(len(SPARK) - 1, int((v - lo) / span * (len(SPARK) - 1)))] for v in values)


def wrap(text: str, width: int, indent: str = "") -> list[str]:
    out = []
    for para in (text or "").splitlines() or [""]:
        out += textwrap.wrap(para, max(10, width - len(indent)), initial_indent=indent, subsequent_indent=indent) or [indent]
    return out


def cells(text: str) -> int:
    """Terminal columns a string takes (wide characters such as emoji take two)."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 0 if unicodedata.combining(ch) else 1 for ch in text)


def ago(iso: str | None) -> str:
    if not iso:
        return "never"
    then = datetime.fromisoformat(iso).replace(tzinfo=None)
    mins = int((datetime.now() - then).total_seconds() // 60)
    return "just now" if mins < 1 else f"{mins}m ago" if mins < 60 else f"{mins // 60}h ago" if mins < 1440 else f"{mins // 1440}d ago"


def heading(label: str, width: int, extra: str = "", extra_style: str = "dim", style: str = "heading") -> Line:
    """A section title with a faint rule running to the right edge."""
    used = len(label) + (len(extra) + 1 if extra else 0)
    return [(label, style), (f" {extra}" if extra else "", extra_style), (" " + "─" * max(0, width - used - 1), "faint")]


def meter(score: int, band: str, cells_: int = 12) -> Line:
    filled = round(max(0, min(100, score)) / 100 * cells_)
    style = BAND_STYLE.get(band, "dim")
    return [("━" * filled, style), ("━" * (cells_ - filled), "faint"), (f" {score}", style), (f" {band}", "dim")]


# ---------------------------------------------------------------- renderers

def render_state(state: dict | None, width: int) -> list[Line]:
    """The adaptive-state banner at the top of Today. A steady day needs none."""
    if not state or state.get("id") == "steady":
        return []
    head = f"{STATE_ICON.get(state['id'], '●')} {state['label']}"
    msg = wrap(state.get("message", ""), width - len(head) - 2)
    lines = [[(head, "heading"), ("  " + msg[0], "text")]] + [[(" " * (len(head) + 2) + m, "text")] for m in msg[1:]]
    if state.get("reasons"):
        lines += [[(l, "dim")] for l in wrap(" · ".join(state["reasons"]), width, " " * (len(head) + 2))]
    return lines + [[]]


def render_today(t: dict, width: int, now: datetime | None = None, state: dict | None = None) -> list[Line]:
    now = now or datetime.now()
    lines: list[Line] = render_state(state, width)
    if t.get("missing"):
        lines += [[("No brief yet today.", "title")],
                  [("Press ", "dim"), ("b", "key"), (" to gather it now (about 20s), or wait for the morning run.", "dim")], []]
    else:
        load = t.get("load") or {}
        lines += [[(l, "title")] for l in wrap(t.get("headline") or "", width)]
        lines.append([(f"{len(t.get('events', []))} meetings · {t.get('meeting_min', 0) / 60:.1f}h", "dim"), ("     load ", "dim"),
                      *meter(load.get("score", 0), load.get("band", "ok"))])
        lines += [[], heading("Schedule", width)]
        hhmm = now.strftime("%H:%M")
        if not t.get("events"):
            lines.append([("  nothing on the calendar", "dim")])
        drew_now = False
        for e in t.get("events", []):
            if not drew_now and e["start"] > hhmm:
                lines.append([(f"  {hhmm} ── now ──", "now")])
                drew_now = True
            style = "dim" if e["end"] <= hhmm else "focus" if e["title"].startswith("🎯") else "text"
            lines.append([(f"  {e['start']}–{e['end']}  ", "dim"), (e["title"], style),
                          (f"  ({e['attendees']})" if e.get("attendees", 0) > 1 else "", "dim")])
        if t.get("focus_windows"):
            lines.append([("  focus windows  ", "dim"), (", ".join(f"{w['start']}–{w['end']}" for w in t["focus_windows"]), "focus")])
        lines.append([])
    plan = t.get("plan")
    if plan and plan.get("top3"):
        done = sum(1 for x in plan["top3"] if x["done"])
        lines.append(heading("Top 3", width, f"{done}/{len(plan['top3'])}", "ok" if done == len(plan["top3"]) else "dim"))
        lines += [[("  ✓ " if x["done"] else "  ○ ", "ok" if x["done"] else "dim"), (x["title"], "dim" if x["done"] else "text")]
                  for x in plan["top3"]]
    elif t.get("top3_proposed"):
        lines.append(heading("Proposed Top 3", width, "press p to plan"))
        for i, x in enumerate(t["top3_proposed"], 1):
            lines.append([(f"  {i}. ", "dim"), (x["title"], "text"), (f"  {x.get('why', '')}", "dim")])
    if t.get("risks"):
        lines += [[], heading("Risks", width, style="warn")] + [[("  • ", "warn"), (r, "text")] for r in t["risks"]]
    if t.get("proposals_pending"):
        lines += [[], [(f"{t['proposals_pending']} approvals waiting", "warn"), ("  press 2 to review", "dim")]]
    return lines


def render_approvals(items: list[dict], width: int) -> tuple[list[Line], list[int]]:
    if not items:
        return [[("Nothing waiting.", "title")], [("The agents queue anything outward-facing here for you to approve.", "dim")]], []
    lines: list[Line] = [[(f"{len(items)} waiting", "dim"), (" · ", "faint"), (f"{sum(1 for p in items if p['safe'])} safe to approve in one go (A)", "dim")], []]
    rows = []
    for p in items:
        rows.append(len(lines))
        lines.append([(f"{p['agent']}/{p['action']}", "heading"), ("  safe" if p["safe"] else "  outward", "ok" if p["safe"] else "warn"),
                      (f"  {ago(p['created'])}" if p.get("created") else "", "dim")])
        lines.append([("  " + p["preview"].splitlines()[0][: max(10, width - 4)], "text")])
        if p.get("reason"):
            lines.append([("  why  ", "faint"), (p["reason"][: max(10, width - 9)], "dim")])
        lines.append([])
    return lines, rows


def render_agents(a: dict, width: int) -> tuple[list[Line], list[int]]:
    lines: list[Line] = [[("Paused", "bad"), (": every scheduled job is off. P resumes them.", "dim")] if a.get("paused") else
                         [("Spend ", "dim"), (f"${a.get('spend_today', 0):.2f}", "text"), (" today · ", "dim"),
                          (f"${a.get('spend_7d', 0):.2f}", "text"), (" last 7 days", "dim")], [],
                         [(f"  {'Agent':<20}{'Schedule':<26}{'Last run':<10}   Last 7 days", "faint")]]
    rows = []
    for ag in a.get("agents", []):
        rows.append(len(lines))
        lines.append([("● ", STATUS_STYLE.get(ag["status"], "warn")), (f"{ag['label']:<20}", "text"),
                      (f"{ag['schedule']:<26}", "dim"), (f"{ago(ag['last_run']):<10}", "dim"),
                      (f"{ag['runs_7d']:>3} runs  ${ag['cost_7d']:.2f}", "dim")])
        if ag.get("status") == "error" and ag.get("last_error"):
            lines.append([("    " + ag["last_error"][: width - 6], "bad")])
    return lines, rows


def render_wellbeing(w: dict, width: int) -> list[Line]:
    today = w.get("today") or {}
    lines: list[Line] = [heading("Load today", width), [("  ", "text"), *meter(today.get("score", 0), today.get("band", "ok"))]]
    lines += [[("  • ", "dim"), (r, "text")] for r in today.get("reasons", [])] or [[("  nothing heavy so far", "dim")]]
    days = w.get("days", [])
    if days:
        lines += [[], heading(f"Last {len(days)} days", width, f"{days[0]['date'][5:]} → {days[-1]['date'][5:]}")]
        series = (("mood", [d["mood"] for d in days], 1, 5), ("energy", [d["energy"] for d in days], 1, 5),
                  ("load", [d["load"] for d in days], 0, 100),
                  ("keyboard h", [round(d["active_min"] / 60, 1) for d in days], 0, None),
                  ("meetings h", [round(d["meeting_min"] / 60, 1) for d in days], 0, None))
        for label, values, lo, hi in series:
            last = next((v for v in reversed(values) if v is not None), None)
            lines.append([(f"  {label:<12}", "dim"), (sparkline(values, lo, hi), "accent"), (f"  {last if last is not None else '–'}", "text")])
    if w.get("checkins_today"):
        lines += [[], heading("Check-ins today", width)]
        lines += [[("  " + c["ts"][11:16] + "  ", "dim"), (" · ".join(f"{k} {c[k]}" for k in ("mood", "energy", "stress") if c.get(k)), "text")]
                  for c in w["checkins_today"]]
    if w.get("experiment"):
        lines += [[], heading("This week's experiment", width)] + [[(l, "text")] for l in wrap(w["experiment"], width, "  ")]
    if w.get("wins"):
        lines += [[], heading("Wins jar", width)] + [[(f"  {x['date'][5:]}  ", "dim"), (x["text"], "text")] for x in w["wins"][:8]]
    lines += [[], [("Press ", "dim"), ("c", "key"), (" to check in. Your data stays on this computer.", "dim")]]
    return lines


def render_setup(s: dict, width: int) -> tuple[list[Line], list[int]]:
    found = s.get("items", [])
    ok = sum(1 for i in found if i["status"] == "ok")
    lines: list[Line] = [[("Setup ", "title"), (f"{ok}/{len(found)} ready", "ok" if s.get("complete") else "warn"),
                          ("   ✓ complete" if s.get("complete") else "", "ok")]]
    p = s.get("profile", {})
    lines.append([(f"{p.get('name') or '(no name yet)'} · GitHub {p.get('github_user') or '–'} · Jira {p.get('jira_site') or '–'}", "dim")])
    rows, group = [], None
    for item in found:
        if item["group"] != group:
            group = item["group"]
            members = [i for i in found if i["group"] == group]
            done = sum(1 for i in members if i["status"] == "ok")
            lines += [[], heading(group, width, f"{done}/{len(members)}", "ok" if done == len(members) else "dim")]
        rows.append(len(lines))
        action = f"  [{item['action']['label']}]" if item.get("action") else ""
        room = max(10, width - 32 - len(action))
        detail = item["detail"] if len(item["detail"]) <= room else item["detail"][: room - 1] + "…"
        lines.append([(f"  {ICONS[item['status']]} ", STATUS_STYLE[item["status"]]), (f"{item['label']:<26}", "text"),
                      (detail, "dim"), (action, "key")])
    if s.get("ntfy", {}).get("topic"):
        lines += [[], heading("Phone", width), [("  Install ntfy and subscribe to ", "dim"), (s["ntfy"]["topic"], "key")],
                  [("  Mission Control's Setup screen shows a QR code for this.", "dim")]]
    return lines, rows


def tool_label(block: dict) -> str:
    """A short label for a tool call: "Read Daily/2026-11-02.md", "Grep Ravi", "Atlassian search"."""
    name, args = block.get("name", ""), block.get("input") or {}
    if name.startswith("mcp__"):
        parts = name.split("__")
        server = parts[1].removeprefix("claude_ai_").replace("_", " ")
        return f"{server} {re.sub(r'(?<!^)(?=[A-Z])', ' ', parts[-1]).split()[0].lower()}"
    arg = args.get("file_path") or args.get("pattern") or args.get("path") or ""
    if "/" in arg and name == "Read":
        arg = "/".join(arg.split("/")[-2:])
    return f"{name} {arg}".strip()[:60]


def _markdown(text: str, width: int, indent: str) -> list[Line]:
    """Just enough Markdown for a terminal: headings, bullets, no ** or backticks."""
    lines: list[Line] = []
    for raw in text.splitlines():
        line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", raw).replace("**", "").replace("`", "")
        if not line.strip():
            lines.append([])
        elif line.lstrip().startswith("#"):
            lines += [[(l, "title")] for l in wrap(line.lstrip("# "), width, indent)]
        elif re.match(r"\s*([-*]|\d+\.) ", line):
            lead = len(line) - len(line.lstrip())
            body = re.sub(r"^\s*[-*] ", "• ", line).lstrip()
            sub = indent + " " * lead
            wrapped = textwrap.wrap(body, max(10, width - len(sub)), initial_indent=sub, subsequent_indent=sub + "  ")
            lines += [[(l, "text")] for l in wrapped]
        else:
            lines += [[(l, "text")] for l in wrap(line, width, indent)]
    return lines


def render_chat(messages: list[dict], width: int, spin: str = "⠋") -> list[Line]:
    if not messages:
        lines: list[Line] = [[("Ask me anything", "title")]]
        lines += [[(l, "dim")] for l in wrap("I can read your notes, memory, calendar, Jira and GitHub, and answer here. "
                                             "Read-only: I look things up, I don't change anything.", width)]
        lines += [[], [("Try", "heading")]] + [[("  › ", "accent"), (h, "text")] for h in CHAT_HINTS]
        return lines + [[], [("Press ", "dim"), ("/", "key"), (" or ", "dim"), ("⏎", "key"), (" to type.", "dim")]]
    return render_messages(messages, width, spin)


def render_messages(messages: list[dict], width: int, spin: str = "⠋") -> list[Line]:
    lines: list[Line] = []
    for m in messages:
        if m["role"] == "you":
            first, *rest = wrap(m["text"], width - 5) or [""]
            lines += [[("you  ", "accent"), (first, "title")]] + [[("     " + r, "title")] for r in rest]
        elif m["role"] == "note":
            lines += [[(l, "warn")] for l in wrap(m["text"], width, "     ")]
        else:
            lines.append([("me", "heading"), ("   " + " · ".join(m["tools"][-4:]) if m.get("tools") else "", "faint")])
            lines += _markdown(m.get("text", ""), width, "     ")
            if m.get("status") == "thinking":
                lines.append([(f"     {spin} ", "accent"), ("looking things up…" if m.get("tools") else "thinking…", "dim")])
            if m.get("error"):
                lines.append([("     " + m["error"][: width - 5], "dim" if m["error"] == "cancelled" else "bad")])
            elif m.get("status") == "done" and m.get("cost") is not None:
                lines.append([(f"     ${m['cost']:.2f}", "faint")])
        lines.append([])
    return lines


def render_ritual(s, width: int, spin: str = "⠋") -> tuple[list[Line], list[int]]:
    """The Plan tab: the conversation, then the checklist once Claude sends a plan, shutdown or entry."""
    if s is None:
        lines: list[Line] = [[("Plan, shut down or write your diary, right here", "title")]]
        lines += [[(l, "dim")] for l in wrap("A conversation with your chief of staff. It looks things up but changes "
                                             "nothing until you approve a checklist: focus blocks, Jira moves and the "
                                             "Work Log happen only if you leave them ticked.", width)]
        lines += [[], [("  p  ", "key"), ("plan the day", "text")], [("  s  ", "key"), ("shut down the day", "text")],
                  [("  d  ", "key"), ("write your diary", "text")], [("  V  ", "key"), ("diary by voice (opens Claude Code)", "dim")]]
        return lines, []
    if s.stage == "gathering" and not s.messages:
        return [[(f"{spin} ", "accent"), (RITUAL_WAIT.get(s.kind, "starting…"), "dim")]], []
    lines = render_messages(s.messages, width, spin)
    rows: list[int] = []
    if s.stage not in ("review", "applying", "done") or not s.items:
        return lines, rows
    lines.append(heading({"review": "Ready when you are", "applying": "Making the changes", "done": "Done"}[s.stage], width))
    if s.kind == "diary" and s.data:
        lines += _markdown(s.data["entry"], width, "    ") + [[]]
    for item in s.items:
        if s.stage == "review":
            rows.append(len(lines))
        if item["status"] == "running":
            mark = (f"  {spin}   ", "accent")
        elif item["status"] in ITEM_MARK:
            icon, style = ITEM_MARK[item["status"]]
            mark = (f"  {icon}   ", style)
        else:
            mark = ("  [x] " if item["checked"] else "  [ ] ", "accent" if item["checked"] else "faint")
        lines.append([mark, (item["label"], "text" if item["checked"] else "dim"),
                      ("  private · always" if item["fixed"] else "", "faint")])
        detail = item["result"] if item["status"] not in ("", "running") else item["detail"]
        if detail:
            style = {"failed": "bad", "warn": "warn"}.get(item["status"], "dim")
            lines.append([("      " + detail[: max(10, width - 6)], style)])
    if s.stage == "done":
        lines += [[], [(RITUAL_AFTER[s.kind], "dim")]]
    return lines, rows


# ---------------------------------------------------------------- app

class App:
    def __init__(self, scr, cfg: Config, start_tab: int = 0):
        self.scr, self.cfg, self.tab = scr, cfg, start_tab
        self.sel = {i: 0 for i in range(len(TABS))}
        self.scroll = {i: 0 for i in range(len(TABS))}
        self.data: dict[int, object] = {}
        self.loading: set[int] = set()
        self.message = ""
        self.job: threading.Thread | None = None
        self.job_label = ""
        self.status = ""
        self.lock = threading.Lock()
        self.theme_state: dict | None = None  # the live adaptive state, loaded in the background
        self.chat: list[dict] = []  # this session's conversation; never written to disk by the UI
        self.chat_session: str | None = None
        self.chat_job: threading.Thread | None = None
        self.chat_cancel = threading.Event()
        self.follow = {CHAT: True, RITUAL: True}  # pinned to the newest line until the user scrolls up
        self.session: rituals.Session | None = None  # the plan, shutdown or diary in the Plan tab
        self.session_job: threading.Thread | None = None
        self._pending_palette: dict | None = None
        self._picking = False
        self._sys_mode = theme.terminal_mode("system")
        self.palette = self._resolve()
        self._colors()

    # -- colours
    def _resolve(self, choice: dict | None = None) -> dict:
        """The palette for the saved prefs, overridden by `choice` while the picker is open."""
        p = {**theme.prefs(self.cfg), "preview": None, **(choice or {})}
        mode = self._sys_mode if p["mode"] == "system" else p["mode"]
        state = theme.STATES[p["preview"]] if p["preview"] else (
            theme.STATES[self.theme_state["id"]] if p["adaptive"] and self.theme_state else None)
        return theme.palette(theme.THEMES[p["name"]], mode, state, p["accent"])

    def _pair(self, fg: int, bg: int = -1) -> int:
        if (fg, bg) not in self._pairs:
            if len(self._pairs) + 1 >= curses.COLOR_PAIRS:
                return curses.A_NORMAL
            curses.init_pair(len(self._pairs) + 1, fg, bg)
            self._pairs[(fg, bg)] = len(self._pairs) + 1
        return curses.color_pair(self._pairs[(fg, bg)])

    def _colors(self) -> None:
        """Build the style table from the palette: xterm-256 when the terminal has it, else the 8 basic colours.

        Three layers share the styles: plain (the terminal's own background), sel
        (the selected row) and bar (the top bar and pop-ups)."""
        b = curses.A_BOLD
        plain = {"text": 0, "title": b, "dim": curses.A_DIM, "faint": curses.A_DIM, "ok": 0, "warn": 0, "bad": 0, "now": b,
                 "accent": 0, "heading": b, "key": b, "focus": 0, "tab": curses.A_DIM, "tab_active": b,
                 "brand": curses.A_REVERSE | b}
        self.styles = {"plain": plain, "sel": {k: v | b for k, v in plain.items()}, "bar": dict(plain)}
        self._pairs: dict[tuple[int, int], int] = {}
        self.deep = False
        if not curses.has_colors():
            return
        try:
            curses.start_color()
            curses.use_default_colors()
        except curses.error:
            return
        pal, self.deep = self.palette, curses.COLORS >= 256
        deep = self.deep

        def c(token: str) -> int:
            return theme.xterm256(pal[token]) if deep else theme.basic8(pal[token])
        quiet = 0 if deep else curses.A_DIM
        spec = {"text": (-1, 0), "title": (-1, b), "dim": (c("muted"), quiet), "faint": (c("border-strong") if deep else -1, quiet),
                "ok": (c("ok"), 0), "warn": (c("warn"), 0), "bad": (c("bad"), 0), "now": (c("now"), b),
                "accent": (c("accent"), 0), "heading": (c("accent"), b), "key": (c("accent"), b), "focus": (c("accent-2"), 0),
                "tab": (c("muted"), quiet), "tab_active": (c("accent"), b)}
        layers = {"plain": -1, "sel": c("surface-3") if deep else -1, "bar": c("surface-2") if deep else -1}
        for layer, bg in layers.items():
            for name, (fg, attr) in spec.items():
                self.styles[layer][name] = self._pair(fg, bg) | attr | (b if layer == "sel" and not deep else 0)
            self.styles[layer]["brand"] = (self._pair(c("accent-ink"), c("accent")) | b if deep
                                           else self._pair(c("accent"), -1) | curses.A_REVERSE | b)

    def swatch(self, hex_: str) -> int:
        bg = theme.xterm256(self.palette["surface-2"]) if self.deep else -1
        return self._pair(theme.xterm256(hex_) if self.deep else theme.basic8(hex_), bg)

    def load_theme(self) -> None:
        """Recompute the adaptive state off the main thread; draw() applies the new palette."""
        def work():
            try:
                with self.conn() as conn:
                    self.theme_state = theme.current(self.cfg, conn)
                self._pending_palette = self._resolve()
            except Exception as e:  # noqa: BLE001 - colours are cosmetic, never crash for them
                self.message = f"theme: {e}"
        threading.Thread(target=work, daemon=True).start()

    def conn(self):
        return closing(db.connect(self.cfg.db_path))

    def load(self, tab: int, force: bool = False) -> None:
        if tab in (CHAT, RITUAL) or tab in self.loading or (tab in self.data and not force):
            return
        self.loading.add(tab)

        def work():
            try:
                with self.conn() as conn:
                    value = {
                        0: lambda: server.today_view(self.cfg, conn),
                        1: lambda: proposals.listing(conn, "pending"),
                        2: lambda: server.agents_view(self.cfg, conn),
                        3: lambda: server.wellbeing_view(self.cfg, conn),
                        4: lambda: setup.snapshot(self.cfg, conn, deep=getattr(self, "_deep", False)),
                    }[tab]()
                    if tab == 0:
                        self.status = todaymod.statusline(self.cfg, paused=db.is_paused(conn))
                with self.lock:
                    self.data[tab] = value
            except Exception as e:  # noqa: BLE001 - show it, never crash the UI
                self.message = f"{TABS[tab]}: {e}"
            finally:
                self._deep = False
                self.loading.discard(tab)

        threading.Thread(target=work, daemon=True).start()

    def reload_all(self) -> None:
        for t in range(len(TABS)):
            if t in self.data or t == self.tab:
                self.load(t, force=True)

    def start_job(self, label: str, fn, reload: tuple[int, ...] = ()) -> None:
        if self.job and self.job.is_alive():
            self.message = f"busy: {self.job_label}"
            return

        def work():
            try:
                self.message = f"{label}: {fn() or 'done'}"
            except Exception as e:  # noqa: BLE001
                self.message = f"{label} failed: {e}"
            for t in reload or (self.tab,):
                self.load(t, force=True)

        self.job_label = label
        self.job = threading.Thread(target=work, daemon=True)
        self.job.start()

    def suspend(self, fn) -> None:
        curses.def_prog_mode()
        curses.endwin()
        # Ctrl-C reaches the whole foreground group: let the child (claude, gh) handle it,
        # and don't die with it. A Python handler, unlike SIG_IGN, is reset for the child at exec.
        previous = signal.signal(signal.SIGINT, lambda *_: None)
        try:
            fn()
        finally:
            signal.signal(signal.SIGINT, previous)
            curses.reset_prog_mode()
            self.scr.refresh()
            self.reload_all()

    # -- drawing
    def put(self, y: int, x: int, text: str, style: str | int = "text", extra: int = 0, layer: str = "plain") -> int:
        h, w = self.scr.getmaxyx()
        if y < 0 or y >= h or x >= w:
            return x
        text = text[: max(0, w - x - (1 if y == h - 1 else 0))]
        attr = style if isinstance(style, int) else self.styles[layer].get(style, 0)
        try:
            self.scr.addstr(y, x, text, attr | extra)
        except curses.error:
            pass
        return x + cells(text)

    def _span(self, lines: list[Line], rows: list[int]) -> tuple[int, int] | None:
        """Lines covered by the selected item: from its row to the next item or blank line."""
        if not rows:
            return None
        i = min(self.sel[self.tab], len(rows) - 1)
        end = rows[i + 1] if i + 1 < len(rows) else len(lines)
        return rows[i], next((j for j in range(rows[i] + 1, end) if not lines[j]), end)

    def draw(self, refresh: bool = True) -> None:
        if self._pending_palette and not self._picking:
            if self._pending_palette != self.palette:
                self.palette = self._pending_palette
                self._colors()
            self._pending_palette = None
        self.scr.erase()
        h, w = self.scr.getmaxyx()
        if w < 60 or h < 14:
            self.put(0, 0, "me: make the terminal at least 60×14", "warn")
            self.scr.refresh()
            return
        st = self.theme_state
        calm = bool(st and st.get("calm"))

        # top bar: brand, date, status line, and the adaptive state on the right
        self.put(0, 0, " " * w, "text", layer="bar")
        x = self.put(0, 0, " ◆ me ", "brand", layer="bar")
        x = self.put(0, x + 2, datetime.now().strftime("%a %d %b · %H:%M"), "dim", layer="bar")
        right = w - 1
        if st:
            chip = f"{STATE_ICON.get(st['id'], '●')} {st['label']} · load {st['load']} "
            right = w - cells(chip) - 1
            self.put(0, right, chip, "accent", layer="bar")
        if self.status and x + 4 + cells(self.status) < right - 2:
            self.put(0, right - 3 - cells(self.status), self.status, "dim", layer="bar")

        # tabs, with an accent underline beneath the active one
        self.put(2, 0, "─" * w, "faint")
        pending = len(self.data.get(1) or [])
        setup_data = self.data.get(4) or {}
        tabs = []
        for i, name in enumerate(TABS):
            badge = (f" {pending}" if i == 1 and pending else
                     " ●" if i == 4 and setup_data and not setup_data.get("complete") else
                     " ●" if i == RITUAL and self.session and self.session.stage == "review" else "")
            tabs.append((self.session.title if i == RITUAL and self.session else name, badge))
        need = 2 + sum(len(f"{i + 1} ") + cells(n) + cells(b) for i, (n, b) in enumerate(tabs))
        gap = next((g for g in (4, 3, 2) if need + g * (len(tabs) - 1) <= w), 1)
        x = 2
        for i, (name, badge) in enumerate(tabs):
            active, start = i == self.tab, x
            x = self.put(1, x, f"{i + 1} ", "accent" if active else "faint")
            x = self.put(1, x, name, "tab_active" if active else "tab")
            if badge:
                x = self.put(1, x, badge, "dim" if calm else "accent" if i == RITUAL else "warn")
            if active:
                self.put(2, start - 1, "━" * (x - start + 2), "accent")
            x += gap

        # body
        lines, rows = self.body(w - 4)
        top, height = 4, h - 7
        if self.tab in self.follow:
            bottom = max(0, len(lines) - height)
            if self.follow[self.tab] or self.scroll[self.tab] >= bottom:
                self.scroll[self.tab], self.follow[self.tab] = bottom, True
        span = self._span(lines, rows)
        if span:
            if span[0] < self.scroll[self.tab]:
                self.scroll[self.tab] = span[0]
            elif span[1] > self.scroll[self.tab] + height:
                self.scroll[self.tab] = span[1] - height
        self.scroll[self.tab] = max(0, min(self.scroll[self.tab], max(0, len(lines) - height)))
        for row, line in enumerate(lines[self.scroll[self.tab]: self.scroll[self.tab] + height]):
            idx = self.scroll[self.tab] + row
            layer = "sel" if span and span[0] <= idx < span[1] else "plain"
            if layer == "sel":
                self.put(top + row, 0, " " * w, "text", layer="sel")
                self.put(top + row, 0, "▎", "accent", layer="sel")
            xx = 2
            for text, style in line:
                xx = self.put(top + row, xx, text, style, layer=layer)
        if len(lines) > height:
            shown = f" {self.scroll[self.tab] + 1}–{min(len(lines), self.scroll[self.tab] + height)} of {len(lines)} "
            self.put(h - 3, w - cells(shown) - 2, shown, "faint")

        # footer: message or progress, then key hints (? and q always visible)
        self.put(h - 3, 0, "─" * (w - (cells(shown) + 3 if len(lines) > height else 0)), "faint")
        busy = self.job and self.job.is_alive()
        if self.tab == CHAT and self.chat_job and self.chat_job.is_alive() and not busy:
            self.put(h - 2, 2, "answering… x stops it", "dim")
        elif self.tab == RITUAL and self.session_busy() and not busy:
            self.put(h - 2, 2, {"applying": "making the changes…"}.get(self.session.stage, "thinking… x stops it"), "dim")
        elif busy or self.tab in self.loading:
            spin = SPINNER[int(time.time() * 8) % len(SPINNER)]
            self.put(h - 2, 2, f"{spin} {self.job_label if busy else 'loading ' + TABS[self.tab].lower() + '…'}", "accent")
        elif self.message:
            self.put(h - 2, 2, self.message, "bad" if "failed" in self.message or "not saved" in self.message else "text")
        tail = [("?", "all keys"), ("q", "quit")]
        tail_w = sum(len(k) + len(v) + 3 for k, v in tail)
        x = 2
        for key, label in self.keys():
            if x + len(key) + len(label) + 3 > w - tail_w - 2:
                break
            x = self.put(h - 1, x, key, "key")
            x = self.put(h - 1, x + 1, label, "dim") + 2
        x = w - tail_w - 1
        for key, label in tail:
            x = self.put(h - 1, x, key, "key")
            x = self.put(h - 1, x + 1, label, "dim") + 2
        if refresh:
            self.scr.refresh()

    def body(self, width: int) -> tuple[list[Line], list[int]]:
        if self.tab == CHAT:
            return render_chat(self.chat, width, SPINNER[int(time.time() * 8) % len(SPINNER)]), []
        if self.tab == RITUAL:
            return render_ritual(self.session, width, SPINNER[int(time.time() * 8) % len(SPINNER)])
        data = self.data.get(self.tab)
        if data is None:
            return [[("loading…", "dim")]], []
        if self.tab == 0:
            return render_today(data, width, state=self.theme_state), []
        if self.tab == 1:
            return render_approvals(data, width)
        if self.tab == 2:
            return render_agents(data, width)
        if self.tab == 3:
            return render_wellbeing(data, width), []
        return render_setup(data, width)

    def keys(self) -> list[tuple[str, str]]:
        extra = {1: [("⏎", "details"), ("a", "approve"), ("x", "reject"), ("A", "approve safe")],
                 2: [("⏎", "run now"), ("P", "pause/resume")],
                 4: [("⏎", "fix"), ("e", "profile"), ("D", "deep check")],
                 CHAT: [("⏎", "ask"), ("x", "stop / new chat"), ("↑↓", "scroll")]}.get(self.tab, [])
        if self.tab == RITUAL:
            s = self.session
            extra = ([("V", "voice diary")] if not s else
                     [("space", "tick or untick"), ("⏎", "do the ticked ones"), ("/", "change"), ("x", "cancel")] if s.stage == "review" else
                     [("x", "close")] if s.stage == "done" else
                     [("⏎", "reply"), ("x", "stop / end"), ("↑↓", "scroll")])
        return extra + [("/", "chat")] * (self.tab != RITUAL) + [("n", "note"), ("c", "check-in"), ("p", "plan"), ("s", "shutdown"), ("d", "diary"), ("b", "brief"),
                        ("T", "theme"), ("t", "todo"), ("i", "idea"), ("r", "refresh")]

    # -- input
    def read_key(self):
        """Block until a key arrives. The main loop's 250ms timeout makes get_wch raise on silence."""
        while True:
            try:
                return self.scr.get_wch()
            except curses.error:
                continue

    def prompt(self, label: str, initial: str = "") -> str | None:
        h, w = self.scr.getmaxyx()
        buf = list(initial)
        curses.curs_set(1)
        try:
            while True:
                self.put(h - 2, 0, " " * (w - 1))
                x = self.put(h - 2, 1, label, "accent")
                shown = "".join(buf)[-(w - x - 3):]
                self.put(h - 2, x + 1, shown)
                self.scr.move(h - 2, min(w - 2, x + 1 + len(shown)))
                self.scr.refresh()
                ch = self.read_key()
                if ch in ("\n", "\r", curses.KEY_ENTER):
                    return "".join(buf).strip()
                if ch == "\x1b":
                    return None
                if ch in (curses.KEY_BACKSPACE, "\x7f", "\b"):
                    if buf:
                        buf.pop()
                elif isinstance(ch, str) and ch.isprintable():
                    buf.append(ch)
        finally:
            curses.curs_set(0)

    def confirm(self, text: str) -> bool:
        answer = self.prompt(f"{text} [y/N]")
        return bool(answer) and answer.lower().startswith("y")

    def show_text(self, title: str, text: str) -> None:
        h, w = self.scr.getmaxyx()
        lines = wrap(text, w - 6)
        offset = 0
        while True:
            self.scr.erase()
            self.put(0, 0, " " * w, "text", layer="bar")
            self.put(0, 2, title[: w - 4], "heading", layer="bar")
            for row, line in enumerate(lines[offset: offset + h - 4]):
                self.put(row + 2, 3, line, "text")
            self.put(h - 2, 0, "─" * w, "faint")
            self.put(h - 1, 2, "↑↓ scroll · any other key to close", "dim")
            self.scr.refresh()
            ch = self.read_key()
            if ch == curses.KEY_DOWN and offset < len(lines) - (h - 4):
                offset += 1
            elif ch == curses.KEY_UP and offset > 0:
                offset -= 1
            elif ch not in (curses.KEY_DOWN, curses.KEY_UP, curses.KEY_RESIZE):
                return

    def selected(self):
        data = self.data.get(self.tab)
        if self.tab == 1 and data:
            return data[min(self.sel[1], len(data) - 1)]
        if self.tab == 2 and data:
            agents = data.get("agents", [])
            return agents[min(self.sel[2], len(agents) - 1)] if agents else None
        if self.tab == 4 and data:
            found = data.get("items", [])
            return found[min(self.sel[4], len(found) - 1)] if found else None
        return None

    def voice_diary(self) -> None:
        """V: the diary in Claude Code, where hold-to-talk voice works."""
        claude = shutil.which("claude")
        if not claude:
            self.message = "claude CLI not found on PATH"
            return
        argv = [claude, "--settings", str(self.cfg.claude_settings), "--model", self.cfg.model("interactive")]
        if not setup._plugin_installed():
            argv += ["--plugin-dir", str(self.cfg.plugin_dir)]

        def launch():
            print("\n  Opening Claude Code for your diary. Hold the voice key to talk.\n"
                  "  Type /exit when you're done to come back to me.\n", flush=True)
            subprocess.call(argv + ["/me:diary"], cwd=self.cfg.home)
        self.suspend(launch)
        self.message = "back from your diary"

    # -- plan, shutdown and diary in the Plan tab
    def session_busy(self) -> bool:
        return bool(self.session_job and self.session_job.is_alive())

    def _session_run(self, fn, after=None) -> None:
        """Run fn(session, conn) on a worker thread with its own db connection."""
        s = self.session

        def work():
            try:
                with self.conn() as conn:
                    fn(s, conn)
            except Exception as e:  # noqa: BLE001 - show it in the conversation, never crash the UI
                s.messages.append({"role": "note", "text": f"Something went wrong: {e}"})
                if s.stage == "gathering":
                    s.stage = "talk"
            finally:
                if after:
                    after(s)

        self.session_job = threading.Thread(target=work, daemon=True)
        self.session_job.start()

    def start_ritual(self, kind: str) -> None:
        s = self.session
        if s and (self.session_busy() or (s.kind == kind and s.stage != "done")):
            self.tab = RITUAL
            if s.kind != kind:
                self.message = f"still working on your {s.kind}"
            return
        if s and s.stage != "done" and any(m["role"] == "you" for m in s.messages) and \
                not self.confirm(f"Leave your {s.kind}? Nothing from it is saved"):
            return
        self.session = rituals.Session(self.cfg, kind, label=tool_label)
        self.tab, self.sel[RITUAL], self.follow[RITUAL] = RITUAL, 0, True
        self._session_run(lambda s, conn: s.open(conn))

    def ritual_key(self, ch) -> None:
        s = self.session
        if s is None:
            if ch == "x":
                self.message = "nothing to close; p plans, s shuts down, d starts the diary"
            return
        if ch == "x":
            if self.session_busy():
                if s.stage == "applying":
                    self.message = "finishing the changes; a moment"
                else:
                    s.cancel.set()
                    self.message = "stopped"
            elif s.stage == "done" or not any(m["role"] == "you" for m in s.messages) or \
                    self.confirm(f"End the {s.kind} without saving?"):
                self.session = None
                self.message = f"{s.kind} closed"
            return
        if self.session_busy():
            self.message = "making the changes…" if s.stage == "applying" else "still thinking; x stops it"
            return
        if ch == " ":
            s.toggle(self.sel[RITUAL])
        elif s.stage == "review" and ch in ("\n", "\r", curses.KEY_ENTER):
            def after(s):
                self.message = s.outcome()
                for t in (0, 1):
                    self.load(t, force=True)
                self.load_theme()
            self._session_run(lambda s, conn: s.apply(conn), after=after)
        elif s.stage == "done":
            self.message = f"this {s.kind} is finished; x closes it"
        else:
            text = self.prompt(f"{s.kind} ›")
            if text:
                self.follow[RITUAL] = True
                self._session_run(lambda s, conn: s.reply(text))

    def setup_action(self, item: dict) -> None:
        if item["id"] in ("profile", "crisis"):
            self.edit_profile()
            return
        action = item.get("action")
        if not action:
            self.message = f"{item['label']}: nothing to do"
            return
        kind = action["kind"]
        if kind == "link":
            self.message = f"opened {action['url']}" if host.open_url(action["url"]) else f"open {action['url']}"
            return
        if kind == "confirm" and not self.confirm(action["confirm"]):
            self.message = "cancelled"
            return
        if kind == "terminal":
            def signin():
                with self.conn() as conn:
                    out = setup.run_action(self.cfg, conn, action["id"], interactive=True)
                input(f"\n{out['detail']}. Press Enter to return to me…")
            self.suspend(signin)
            return

        def work():
            with self.conn() as conn:
                out = setup.run_action(self.cfg, conn, action["id"])
            self.cfg = setup.load(self.cfg.home / "me.toml", self.cfg.home)
            return out["detail"] if out["ok"] else f"failed: {out['detail']}"

        self.start_job(action["label"] + " " + item["label"].lower(), work, reload=(4, 0))

    def edit_profile(self) -> None:
        p = (self.data.get(4) or {}).get("profile") or setup.profile(self.cfg)
        updates = {}
        name = self.prompt("What should I call you?", p.get("name", ""))
        if name is None:
            return
        updates["name"] = name
        country = self.prompt("Helpline country (2 letters, blank to skip):", p.get("crisis_country", ""))
        if country is None:
            return
        updates["crisis_country"] = country
        vaults = setup.obsidian_vaults()
        listing = "  ".join(f"{i}={v['name']}" for i, v in enumerate(vaults, 1))
        for key, label in (("private_vault", "Private vault (diary, daily notes)"), ("public_vault", "Public notes vault, or - for none")):
            answer = self.prompt(f"{label} {listing} or a path:", p.get(key, ""))
            if answer is None:
                return
            if answer.isdigit() and 1 <= int(answer) <= len(vaults):
                answer = vaults[int(answer) - 1]["path"]
            updates[key] = "" if answer == "-" else answer
        try:
            self.cfg = setup.save_profile(self.cfg, updates)
            self.message = "profile saved"
        except Exception as e:  # noqa: BLE001 - bad path, bad country code, unreadable me.toml
            self.message = f"profile not saved: {e}"
        self.load(4, force=True)

    def capture(self, kind: str) -> None:
        text = self.prompt(f"{kind}:")
        if text:
            path = vault.capture(self.cfg, kind, text)
            self.message = f"saved to {path.name}"

    def checkin(self) -> None:
        answer = self.prompt("mood energy stress (1-5 each, e.g. 4 3 2):")
        if not answer:
            return
        try:
            nums = [int(x) for x in answer.split()[:3]]
            note = self.prompt("anything on your mind? (optional)") or None
            with self.conn() as conn:
                msg = wellbeing.record_checkin(self.cfg, conn, *(nums + [None] * (3 - len(nums))), note=note, source="tui")
        except ValueError as e:
            self.message = f"check-in not saved: {e}"
            return
        if msg:
            self.show_text("You're not alone", msg)
        self.message = "checked in, thank you"
        self.load(3, force=True)
        self.load(0, force=True)
        self.load_theme()

    def appearance(self) -> None:
        """T: pick theme, mode and adaptive colours, previewing live; Enter saves to me.toml."""
        p = theme.prefs(self.cfg)
        choice = {"name": p["name"], "mode": p["mode"], "adaptive": p["adaptive"], "preview": None}
        names, previews = list(theme.THEMES), [None, *theme.STATES]
        row, before = 0, self.palette
        self._picking = True
        self.scr.timeout(-1)
        try:
            while True:
                self.palette = self._resolve(choice)
                self._colors()
                self.draw(refresh=False)
                self._draw_appearance(choice, row)
                ch = self.read_key()
                if ch in (curses.KEY_UP, "k"):
                    row = (row - 1) % 4
                elif ch in (curses.KEY_DOWN, "j", "\t"):
                    row = (row + 1) % 4
                elif ch in (curses.KEY_LEFT, curses.KEY_RIGHT, "h", "l", " "):
                    step = -1 if ch in (curses.KEY_LEFT, "h") else 1
                    if row == 0:
                        choice["name"] = names[(names.index(choice["name"]) + step) % len(names)]
                    elif row == 1:
                        choice["mode"] = theme.MODES[(theme.MODES.index(choice["mode"]) + step) % len(theme.MODES)]
                    elif row == 2:
                        choice["adaptive"] = not choice["adaptive"]
                    else:
                        choice["preview"] = previews[(previews.index(choice["preview"]) + step) % len(previews)]
                elif ch in ("\n", "\r", curses.KEY_ENTER):
                    self.cfg = theme.save_prefs(self.cfg, {k: choice[k] for k in ("name", "mode", "adaptive")})
                    self.palette = self._resolve()
                    self.message = (f"theme saved: {theme.THEMES[choice['name']].label}, {choice['mode']}, "
                                    f"adaptive {'on' if choice['adaptive'] else 'off'}")
                    return
                elif ch in ("\x1b", "q", "T"):
                    self.palette = before
                    self.message = "theme unchanged"
                    return
        finally:
            self._picking = False
            self._colors()
            self.scr.timeout(250)

    def _draw_appearance(self, choice: dict, row: int) -> None:
        h, w = self.scr.getmaxyx()
        bw, bh = min(66, w - 4), 13
        y0, x0 = max(1, (h - bh) // 2), (w - min(66, w - 4)) // 2
        for y in range(bh):
            edge = "╭╮" if y == 0 else "╰╯" if y == bh - 1 else "││"
            fill = "─" if y in (0, bh - 1) else " "
            self.put(y0 + y, x0, edge[0] + fill * (bw - 2) + edge[1], "faint", layer="bar")
        self.put(y0, x0 + 2, " Appearance ", "heading", layer="bar")
        t = theme.THEMES[choice["name"]]
        live = self.theme_state["label"] if self.theme_state else "loading"
        here = self._sys_mode if choice["mode"] == "system" else choice["mode"]
        fields = [("Theme", t.label, ""),
                  ("Mode", choice["mode"], f"{here} in this terminal" if choice["mode"] == "system" else ""),
                  ("Adaptive", "on" if choice["adaptive"] else "off",
                   "follows your workload and check-ins" if choice["adaptive"] else "always the theme's own accent"),
                  ("Preview", theme.STATES[choice["preview"]].label if choice["preview"] else f"live: {live}", "only to look, not saved")]
        for i, (label, value, note) in enumerate(fields):
            y, sel = y0 + 2 + i * 2, i == row
            x = self.put(y, x0 + 2, ("▸ " if sel else "  ") + f"{label:<10}", "key" if sel else "dim", layer="bar")
            x = self.put(y, x, f"‹ {value} ›", "tab_active" if sel else "text", layer="bar") + 2
            if i == 0:
                for token in ("bg", "surface", "accent", "accent-2"):
                    x = self.put(y, x, "██", self.swatch(self.palette[token]), layer="bar") + 1
                self.put(y + 1, x0 + 14, t.description[: bw - 16], "dim", layer="bar")
            else:
                self.put(y, x, note[: max(0, x0 + bw - x - 2)], "dim", layer="bar")
        hint = "↑↓ choose · ←→ change · ⏎ save · esc cancel"
        self.put(y0 + bh - 2, x0 + 4, hint[: bw - 6], "dim", layer="bar")
        self.scr.refresh()

    def chat_ask(self) -> None:
        if self.chat_job and self.chat_job.is_alive():
            self.message = "still answering; x stops it"
            return
        question = self.prompt("ask ›")
        if not question:
            return
        self.chat.append({"role": "you", "text": question})
        if wellbeing.CRISIS.search(question):
            self.chat.append({"role": "note", "text": wellbeing.crisis_message(self.cfg)})
        reply = {"role": "me", "text": "", "tools": [], "status": "thinking"}
        self.chat.append(reply)
        self.follow[CHAT] = True
        self.chat_cancel = threading.Event()

        def emit(event: dict) -> None:
            if event.get("type") != "assistant":
                return
            for block in (event.get("message") or {}).get("content") or []:
                if block.get("type") == "text" and block.get("text", "").strip():
                    reply["text"] += ("\n\n" if reply["text"] else "") + block["text"].strip()
                elif block.get("type") == "tool_use":
                    reply["tools"].append(tool_label(block))

        def work():
            try:
                r = ask.stream(self.cfg, question, None, self.chat_session, emit, cancel=self.chat_cancel)
                if r.ok:
                    self.chat_session = r.session_id or self.chat_session
                    reply["cost"] = r.cost_usd
                    reply["text"] = reply["text"] or r.text
                else:
                    reply["error"] = r.error or "no answer"
            except Exception as e:  # noqa: BLE001 - show it in the transcript
                reply["error"] = str(e)
            finally:
                reply["status"] = "done"

        self.chat_job = threading.Thread(target=work, daemon=True)
        self.chat_job.start()

    def chat_stop_or_clear(self) -> None:
        if self.chat_job and self.chat_job.is_alive():
            self.chat_cancel.set()
            self.message = "stopped"
        else:
            self.chat, self.chat_session, self.follow[CHAT] = [], None, True
            self.message = "new chat"

    def handle(self, ch) -> bool:
        tab = self.tab
        if ch in ("q", "Q"):
            return False
        if ch in ("1", "2", "3", "4", "5", "6", "7"):
            self.tab = int(ch) - 1
        elif ch in ("\t", curses.KEY_RIGHT):
            self.tab = (self.tab + 1) % len(TABS)
        elif ch in (curses.KEY_BTAB, curses.KEY_LEFT):
            self.tab = (self.tab - 1) % len(TABS)
        elif ch in (curses.KEY_DOWN, "j"):
            self.sel[tab] += 1
            self._clamp()
        elif ch in (curses.KEY_UP, "k"):
            self.sel[tab] = max(0, self.sel[tab] - 1)
            if tab in (0, 3, CHAT) or (tab == RITUAL and not self.body(self.scr.getmaxyx()[1] - 4)[1]):
                self.scroll[tab] = max(0, self.scroll[tab] - 1)
                if tab in self.follow:
                    self.follow[tab] = False
        elif ch == "r":
            self.load(tab, force=True)
            self.message = "refreshing"
        elif ch in ("n", "t", "i"):
            self.capture({"n": "note", "t": "todo", "i": "idea"}[ch])
        elif ch == "c":
            self.checkin()
        elif ch in RITUALS:
            self.start_ritual(RITUALS[ch])
        elif ch == "V":
            self.voice_diary()
        elif ch == "b":
            self.start_job("gathering your day", lambda: self._brief(), reload=(0,))
        elif ch == "T":
            self.appearance()
        elif ch == "?":
            self.show_text("Keys", HELP)
        elif tab == RITUAL and ch in ("/", " ", "x", "\n", "\r", curses.KEY_ENTER):
            self.ritual_key(ch)
        elif ch == "/" or (tab == CHAT and ch in ("\n", "\r", curses.KEY_ENTER)):
            self.tab = CHAT
            self.draw()
            self.chat_ask()
        elif tab == CHAT and ch == "x":
            self.chat_stop_or_clear()
        elif ch in ("\n", "\r", curses.KEY_ENTER):
            self.enter()
        elif tab == 1 and ch in ("a", "x", "A"):
            self.approval_key(ch)
        elif tab == 2 and ch == "P":
            self._toggle_pause()
        elif tab == 4 and ch == "e":
            self.edit_profile()
        elif tab == 4 and ch == "D":
            self._deep = True
            self.load(4, force=True)
            self.message = "checking connectors (free, ~3s)"
        if self.tab != tab:
            self.load(self.tab)
        return True

    def _clamp(self) -> None:
        _, rows = self.body(self.scr.getmaxyx()[1] - 2)
        if rows:
            self.sel[self.tab] = min(self.sel[self.tab], len(rows) - 1)
        else:
            self.scroll[self.tab] += 1

    def _brief(self) -> str:
        with self.conn() as conn:
            t = briefmod.run(self.cfg, conn)
        return t.get("headline") or "brief written"

    def _toggle_pause(self) -> None:
        with self.conn() as conn:
            paused = not db.is_paused(conn)
            db.set_cursor(conn, "paused", "1" if paused else "0")
        self.message = "scheduled jobs paused" if paused else "scheduled jobs resumed"
        self.load(2, force=True)

    def enter(self) -> None:
        item = self.selected()
        if not item:
            return
        if self.tab == 1:
            self.show_text(f"{item['agent']}/{item['action']} · {item['target']}",
                           f"{item['preview']}\n\nWhy: {item.get('reason') or '-'}\nUndo: {item.get('undo') or '-'}\n\n"
                           "Close, then press a to approve or x to reject.")
        elif self.tab == 2:
            server.run_agent(self.cfg, item["name"])
            self.message = f"started {item['label']} in the background"
        elif self.tab == 4:
            self.setup_action(item)

    def approval_key(self, ch: str) -> None:
        if ch == "A":
            def work():
                with self.conn() as conn:
                    return f"{len(proposals.approve_safe(self.cfg, conn))} safe actions approved"
            self.start_job("approving safe actions", work, reload=(1, 0))
            return
        item = self.selected()
        if not item:
            return
        if ch == "x":
            with self.conn() as conn:
                proposals.reject(conn, item["id"])
            self.message = f"rejected {item['action']}"
            self.load(1, force=True)
            return
        if not item["safe"] and not self.confirm(f"Send this {item['action'].replace('_', ' ')} for real?"):
            return

        def work():
            with self.conn() as conn:
                status, result = proposals.approve(self.cfg, conn, item["id"])
            return f"{status}: {result[:80]}"
        self.start_job(f"approving {item['action']}", work, reload=(1, 0))

    def loop(self) -> None:
        curses.curs_set(0)
        self.scr.timeout(250)
        self.load(self.tab)
        self.load(0)
        self.load(1)
        self.load(4)
        self.load_theme()
        last_refresh = time.time()
        while True:
            self.draw()
            try:
                ch = self.scr.get_wch()
            except curses.error:
                ch = None
            if ch == curses.KEY_RESIZE and host.WINDOWS:
                curses.resize_term(0, 0)  # PDCurses only learns the new size when asked
            if ch is not None and ch != curses.KEY_RESIZE and not self.handle(ch):
                return
            if time.time() - last_refresh > 60:
                last_refresh = time.time()
                self.load(0, force=True)
                self.load(1, force=True)
                self.load_theme()


def main(start: str = "Today") -> int:
    locale.setlocale(locale.LC_ALL, "")
    cfg = setup.ensure_config()
    start_tab = TABS.index(start) if start in TABS else 0
    if start_tab == 0 and not setup.profile(cfg)["name"]:
        start_tab = TABS.index("Setup")  # first run: start where it matters
    try:
        curses.wrapper(lambda scr: App(scr, cfg, start_tab).loop())
    except KeyboardInterrupt:  # Ctrl-C means quit, not a traceback
        pass
    return 0
