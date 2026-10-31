"""`me` with no arguments: the full-screen terminal UI (curses, stdlib only).

Tabs: Today · Approvals · Agents · Wellbeing · Setup. Data loads and long work
(brief, approvals, setup actions) run in background threads, so the screen stays
live. Only the main thread draws. Anything that needs the real terminal (plan,
shutdown, diary, sign-ins) suspends the UI and resumes it afterwards.

Rendering is split into pure functions (`render_*`) that turn data into lines of
(text, style) segments, so they can be tested without a terminal.
"""

from __future__ import annotations

import curses
import locale
import shutil
import subprocess
import threading
import time
import textwrap
from contextlib import closing
from datetime import datetime

from . import db, proposals, setup, vault, wellbeing
from . import brief as briefmod
from . import server
from . import today as todaymod
from .config import Config

TABS = ("Today", "Approvals", "Agents", "Wellbeing", "Setup")
SPARK = "▁▂▃▄▅▆▇█"
ICONS = {"ok": "✓", "warn": "!", "todo": "○", "fail": "✗"}
STATUS_STYLE = {"ok": "ok", "warn": "warn", "todo": "accent", "fail": "bad", "error": "bad", "never": "dim"}
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
RITUALS = {"p": ("plan", "/me:plan"), "s": ("shutdown", "/me:shutdown"), "d": ("diary", "/me:diary")}

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


def ago(iso: str | None) -> str:
    if not iso:
        return "never"
    then = datetime.fromisoformat(iso).replace(tzinfo=None)
    mins = int((datetime.now() - then).total_seconds() // 60)
    return "just now" if mins < 1 else f"{mins}m ago" if mins < 60 else f"{mins // 60}h ago" if mins < 1440 else f"{mins // 1440}d ago"


# ---------------------------------------------------------------- renderers

def render_today(t: dict, width: int, now: datetime | None = None) -> list[Line]:
    now = now or datetime.now()
    lines: list[Line] = []
    if t.get("missing"):
        lines += [[("No brief yet today.", "title")], [("Press ", "dim"), ("b", "key"), (" to gather it now (about 20s), or wait for 07:30.", "dim")], []]
    else:
        lines += [[(l, "title")] for l in wrap(t.get("headline") or "", width)]
        load = t.get("load") or {}
        band_style = {"ok": "ok", "elevated": "warn", "high": "bad"}.get(load.get("band"), "dim")
        lines.append([(f"{len(t.get('events', []))} meetings · {t.get('meeting_min', 0) / 60:.1f}h", "dim"),
                      ("   load ", "dim"), (f"{load.get('score', 0)} {load.get('band', '')}", band_style)])
        lines.append([])
        hhmm = now.strftime("%H:%M")
        lines.append([("Schedule", "accent")])
        if not t.get("events"):
            lines.append([("  nothing on the calendar", "dim")])
        drew_now = False
        for e in t.get("events", []):
            if not drew_now and e["start"] > hhmm:
                lines.append([(f"  {hhmm} ── now ──", "warn")])
                drew_now = True
            style = "dim" if e["end"] <= hhmm else "focus" if e["title"].startswith("🎯") else "text"
            lines.append([(f"  {e['start']}–{e['end']}  ", "dim"), (e["title"], style),
                          (f"  ({e['attendees']})" if e.get("attendees", 0) > 1 else "", "dim")])
        if t.get("focus_windows"):
            lines.append([("  focus: " + ", ".join(f"{w['start']}–{w['end']}" for w in t["focus_windows"]), "dim")])
        lines.append([])
    plan = t.get("plan")
    if plan and plan.get("top3"):
        done = sum(1 for x in plan["top3"] if x["done"])
        lines.append([("Top 3 ", "accent"), (f"{done}/{len(plan['top3'])}", "ok" if done == len(plan["top3"]) else "dim")])
        lines += [[("  ✓ " if x["done"] else "  ○ ", "ok" if x["done"] else "dim"), (x["title"], "dim" if x["done"] else "text")]
                  for x in plan["top3"]]
    elif t.get("top3_proposed"):
        lines.append([("Proposed Top 3", "accent"), ("  (p to plan)", "dim")])
        for i, x in enumerate(t["top3_proposed"], 1):
            lines.append([(f"  {i}. ", "dim"), (x["title"], "text"), (f"  {x.get('why', '')}", "dim")])
    if t.get("risks"):
        lines += [[], [("Risks", "warn")]] + [[("  • " + r, "text")] for r in t["risks"]]
    if t.get("proposals_pending"):
        lines += [[], [(f"{t['proposals_pending']} approvals waiting", "warn"), ("  (2 to review)", "dim")]]
    return lines


def render_approvals(items: list[dict], width: int) -> tuple[list[Line], list[int]]:
    if not items:
        return [[("Nothing waiting. The agents will queue anything outward-facing here.", "dim")]], []
    lines, rows = [], []
    for p in items:
        rows.append(len(lines))
        lines.append([(f"{p['agent']}/{p['action']}", "accent"), ("  safe" if p["safe"] else "", "ok"),
                      ("  " + p["preview"].splitlines()[0][: max(10, width - 40)], "text")])
        if p.get("reason"):
            lines.append([("    " + p["reason"], "dim")])
    return lines, rows


def render_agents(a: dict, width: int) -> tuple[list[Line], list[int]]:
    lines: list[Line] = [[("Paused: every scheduled job is off (P to resume)", "bad")] if a.get("paused") else
                         [(f"Spend today ${a.get('spend_today', 0):.2f} · 7 days ${a.get('spend_7d', 0):.2f}", "dim")], []]
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
    band_style = {"ok": "ok", "elevated": "warn", "high": "bad"}.get(today.get("band"), "dim")
    lines: list[Line] = [[("Load today ", "accent"), (f"{today.get('score', 0)}/100 {today.get('band', '')}", band_style)]]
    lines += [[("  • " + r, "text")] for r in today.get("reasons", [])] or [[("  nothing heavy so far", "dim")]]
    days = w.get("days", [])
    if days:
        lines += [[], [(f"Last {len(days)} days", "accent"), (f"  {days[0]['date'][5:]} → {days[-1]['date'][5:]}", "dim")]]
        series = (("mood", [d["mood"] for d in days], 1, 5), ("energy", [d["energy"] for d in days], 1, 5),
                  ("load", [d["load"] for d in days], 0, 100),
                  ("keyboard h", [round(d["active_min"] / 60, 1) for d in days], 0, None),
                  ("meetings h", [round(d["meeting_min"] / 60, 1) for d in days], 0, None))
        for label, values, lo, hi in series:
            last = next((v for v in reversed(values) if v is not None), None)
            lines.append([(f"  {label:<11}", "dim"), (sparkline(values, lo, hi), "accent"), (f"  {last if last is not None else '–'}", "dim")])
    if w.get("checkins_today"):
        lines += [[], [("Check-ins today", "accent")]]
        lines += [[("  " + c["ts"][11:16] + "  " + " · ".join(f"{k} {c[k]}" for k in ("mood", "energy", "stress") if c.get(k)), "text")]
                  for c in w["checkins_today"]]
    if w.get("experiment"):
        lines += [[], [("This week's experiment", "accent")]] + [[(l, "text")] for l in wrap(w["experiment"], width, "  ")]
    if w.get("wins"):
        lines += [[], [("Wins jar", "accent")]] + [[(f"  {x['date'][5:]}  ", "dim"), (x["text"], "text")] for x in w["wins"][:8]]
    lines += [[], [("c to check in · your data stays on this Mac", "dim")]]
    return lines


def render_setup(s: dict, width: int) -> tuple[list[Line], list[int]]:
    found = s.get("items", [])
    ok = sum(1 for i in found if i["status"] == "ok")
    lines: list[Line] = [[("Setup ", "title"), (f"{ok}/{len(found)} ready", "ok" if s.get("complete") else "warn"),
                          ("   ✓ complete" if s.get("complete") else "", "ok")]]
    p = s.get("profile", {})
    lines.append([(f"  {p.get('name') or '(no name yet)'} · GitHub {p.get('github_user') or '–'} · Jira {p.get('jira_site') or '–'}", "dim")])
    lines.append([("  e edit profile · D deep re-check (connectors) · Enter runs the selected fix", "dim")])
    rows, group = [], None
    for item in found:
        if item["group"] != group:
            group = item["group"]
            lines += [[], [(group, "accent")]]
        rows.append(len(lines))
        action = item.get("action")
        lines.append([(f"  {ICONS[item['status']]} ", STATUS_STYLE[item["status"]]), (f"{item['label']:<26}", "text"),
                      (item["detail"][: max(10, width - 52)], "dim"),
                      (f"  [{action['label']}]" if action else "", "key")])
    if s.get("ntfy", {}).get("topic"):
        lines += [[], [("Phone: install ntfy and subscribe to ", "dim"), (s["ntfy"]["topic"], "key")],
                  [("  (Mission Control's Setup screen shows a QR code for this)", "dim")]]
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
        self._colors()

    # -- plumbing
    def _colors(self) -> None:
        self.styles = {k: curses.A_NORMAL for k in ("text", "dim", "ok", "warn", "bad", "accent", "title", "key", "focus")}
        self.styles.update(title=curses.A_BOLD, dim=curses.A_DIM, key=curses.A_BOLD)
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            for n, color in enumerate((curses.COLOR_GREEN, curses.COLOR_YELLOW, curses.COLOR_RED, curses.COLOR_CYAN,
                                       curses.COLOR_MAGENTA), start=1):
                curses.init_pair(n, color, -1)
            self.styles.update(ok=curses.color_pair(1), warn=curses.color_pair(2), bad=curses.color_pair(3),
                               accent=curses.color_pair(4) | curses.A_BOLD, key=curses.color_pair(5) | curses.A_BOLD,
                               focus=curses.color_pair(5))

    def conn(self):
        return closing(db.connect(self.cfg.db_path))

    def load(self, tab: int, force: bool = False) -> None:
        if tab in self.loading or (tab in self.data and not force):
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
        try:
            fn()
        finally:
            curses.reset_prog_mode()
            self.scr.refresh()
            self.reload_all()

    # -- drawing
    def put(self, y: int, x: int, text: str, style: str = "text", extra: int = 0) -> int:
        h, w = self.scr.getmaxyx()
        if y >= h or x >= w:
            return x
        text = text[: max(0, w - x - (1 if y == h - 1 else 0))]
        try:
            self.scr.addstr(y, x, text, self.styles.get(style, 0) | extra)
        except curses.error:
            pass
        return x + len(text)

    def draw(self) -> None:
        self.scr.erase()
        h, w = self.scr.getmaxyx()
        if w < 60 or h < 14:
            self.put(0, 0, "me: make the terminal at least 60×14", "warn")
            self.scr.refresh()
            return
        head = f" me · {datetime.now().strftime('%a %d %b %H:%M')} "
        self.put(0, 0, head.ljust(w), "title", curses.A_REVERSE)
        if self.status:
            self.put(0, max(len(head), w - len(self.status) - 1), self.status, "title", curses.A_REVERSE)
        x = 1
        pending = len(self.data.get(1) or [])
        setup_data = self.data.get(4) or {}
        for i, name in enumerate(TABS):
            label = f" {i + 1} {name}" + (f" ({pending})" if i == 1 and pending else "") + (
                " ●" if i == 4 and setup_data and not setup_data.get("complete") else "") + " "
            x = self.put(1, x, label, "accent" if i == self.tab else "dim", curses.A_REVERSE if i == self.tab else 0) + 1
        self.put(2, 0, "─" * w, "dim")
        lines, rows = self.body(w - 2)
        top, height = 3, h - 6
        sel_line = rows[self.sel[self.tab]] if rows else None
        if sel_line is not None:
            if sel_line < self.scroll[self.tab]:
                self.scroll[self.tab] = sel_line
            elif sel_line >= self.scroll[self.tab] + height:
                self.scroll[self.tab] = sel_line - height + 1
        self.scroll[self.tab] = max(0, min(self.scroll[self.tab], max(0, len(lines) - height)))
        for row, line in enumerate(lines[self.scroll[self.tab]: self.scroll[self.tab] + height]):
            idx = self.scroll[self.tab] + row
            xx = 1
            if idx == sel_line:
                self.put(top + row, 0, "▌", "key")
            for text, style in line:
                xx = self.put(top + row, xx, text, style, curses.A_BOLD if idx == sel_line else 0)
        self.put(h - 3, 0, "─" * w, "dim")
        busy = self.job and self.job.is_alive()
        if busy or self.tab in self.loading:
            spin = SPINNER[int(time.time() * 8) % len(SPINNER)]
            self.put(h - 2, 1, f"{spin} {self.job_label if busy else 'loading ' + TABS[self.tab].lower() + '…'}", "warn")
        else:
            self.put(h - 2, 1, self.message, "text")
        self.put(h - 1, 1, self.keys(), "dim")
        self.scr.refresh()

    def body(self, width: int) -> tuple[list[Line], list[int]]:
        data = self.data.get(self.tab)
        if data is None:
            return [[("loading…", "dim")]], []
        if self.tab == 0:
            return render_today(data, width), []
        if self.tab == 1:
            return render_approvals(data, width)
        if self.tab == 2:
            return render_agents(data, width)
        if self.tab == 3:
            return render_wellbeing(data, width), []
        return render_setup(data, width)

    def keys(self) -> str:
        common = "n note  t todo  i idea  c check-in  p plan  s shutdown  d diary  b brief  r refresh  q quit"
        extra = {1: "↑↓ select  ⏎ details  a approve  x reject  A approve safe   ",
                 2: "↑↓ select  ⏎ run now  P pause/resume   ",
                 4: "↑↓ select  ⏎ fix  e profile  D deep check   "}.get(self.tab, "")
        return extra + common

    # -- input
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
                ch = self.scr.get_wch()
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
        lines = [title, ""] + wrap(text, w - 4)
        offset = 0
        while True:
            self.scr.erase()
            for row, line in enumerate(lines[offset: offset + h - 2]):
                self.put(row, 2, line, "title" if offset + row == 0 else "text")
            self.put(h - 1, 2, "↑↓ scroll · any other key to close", "dim")
            self.scr.refresh()
            ch = self.scr.get_wch()
            if ch == curses.KEY_DOWN and offset < len(lines) - (h - 2):
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

    def run_ritual(self, key: str) -> None:
        name, command = RITUALS[key]
        claude = shutil.which("claude")
        if not claude:
            self.message = "claude CLI not found on PATH"
            return
        argv = [claude, "--settings", str(self.cfg.claude_settings), "--model", self.cfg.model("interactive")]
        if not setup._plugin_installed():
            argv += ["--plugin-dir", str(self.cfg.plugin_dir)]
        self.suspend(lambda: subprocess.call(argv + [command], cwd=self.cfg.home))
        self.message = f"back from {name}"

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
            subprocess.run(["open", action["url"]], capture_output=True)
            self.message = f"opened {action['url']}"
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

    def handle(self, ch) -> bool:
        tab = self.tab
        if ch in ("q", "Q"):
            return False
        if ch in ("1", "2", "3", "4", "5"):
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
            if tab in (0, 3):
                self.scroll[tab] = max(0, self.scroll[tab] - 1)
        elif ch == "r":
            self.load(tab, force=True)
            self.message = "refreshing"
        elif ch in ("n", "t", "i"):
            self.capture({"n": "note", "t": "todo", "i": "idea"}[ch])
        elif ch == "c":
            self.checkin()
        elif ch in RITUALS:
            self.run_ritual(ch)
        elif ch == "b":
            self.start_job("gathering your day", lambda: self._brief(), reload=(0,))
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
        last_refresh = time.time()
        while True:
            self.draw()
            try:
                ch = self.scr.get_wch()
            except curses.error:
                ch = None
            if ch is not None and ch != curses.KEY_RESIZE and not self.handle(ch):
                return
            if time.time() - last_refresh > 60:
                last_refresh = time.time()
                self.load(0, force=True)
                self.load(1, force=True)


def main(start: str = "Today") -> int:
    locale.setlocale(locale.LC_ALL, "")
    cfg = setup.ensure_config()
    start_tab = TABS.index(start) if start in TABS else 0
    if start_tab == 0 and not setup.profile(cfg)["name"]:
        start_tab = TABS.index("Setup")  # first run: start where it matters
    curses.wrapper(lambda scr: App(scr, cfg, start_tab).loop())
    return 0
