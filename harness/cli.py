"""`me` command-line entry point."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path

from . import agents, bootstrap, db, doctor, guard, host, memory, notify, proposals, schedule, sheets, vault, wellbeing
from . import ask as askmod
from . import brief as briefmod
from . import day as daymod
from . import review as reviewmod
from . import today as todaymod
from .config import HOME, ConfigError, load, load_autonomy

RITUALS = {"diary": "/me:diary", "plan": "/me:plan", "shutdown": "/me:shutdown"}


def parse_day(value: str | None) -> date:
    if not value or value == "today":
        return date.today()
    if value == "yesterday":
        return date.today() - timedelta(days=1)
    if value == "tomorrow":
        return date.today() + timedelta(days=1)
    return date.fromisoformat(value)


def plugin_installed() -> bool:
    registry = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
    try:
        return any(k.startswith("me@") for k in json.loads(registry.read_text()).get("plugins", {}))
    except (OSError, ValueError):
        return False


def read_json_stdin() -> dict:
    try:
        data = json.loads(sys.stdin.read())
    except ValueError as e:
        raise ValueError(f"stdin is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("stdin JSON must be an object")
    return data


def skip_scheduled(cfg, conn, day: date) -> str | None:
    if db.is_paused(conn):
        return "paused"
    if not cfg.is_workday(day):
        return "not a workday"
    return None


def cmd_init(args) -> int:
    path, created = bootstrap.create_config(HOME)
    print(f"{'created' if created else 'kept'} {path}")
    cfg = load()
    db.connect(cfg.db_path)
    print(f"state db ready: {cfg.db_path}")
    for line in bootstrap.scaffold_private_vaults(cfg):
        print(f"private vault ready: {line}")
    missing = bootstrap.public_gitignore_missing(cfg)
    if missing:
        print("public vault .gitignore needs private-folder rules; run `me vault guard-public`")
    return 0


def cmd_capture(args) -> int:
    text = sys.stdin.read() if args.text == ["-"] else " ".join(args.text)
    path = vault.capture(load(), args.command, text)
    print(f"saved to {path}")
    return 0


def launch_ritual(name: str) -> int:
    cfg = load()
    claude_bin = shutil.which("claude")
    if not claude_bin:
        print("claude CLI not found on PATH", file=sys.stderr)
        return 1
    argv = [claude_bin, "--settings", str(cfg.claude_settings), "--model", cfg.model("interactive")]
    if not plugin_installed():
        argv += ["--plugin-dir", str(cfg.plugin_dir)]
    argv.append(RITUALS[name])
    os.chdir(cfg.home)
    os.execv(claude_bin, argv)


def cmd_diary(args) -> int:
    return launch_ritual("diary")


def cmd_brief(args) -> int:
    cfg = load()
    now = datetime.now()
    day = parse_day(args.date) if args.date else now.date()
    with closing(db.connect(cfg.db_path)) as conn:
        if args.scheduled and (reason := skip_scheduled(cfg, conn, day)):
            print(f"skipped: {reason}")
            return 0
        if args.if_stale and not todaymod.is_stale(todaymod.load(cfg, day), now):
            print("brief is fresh; skipped (drop --if-stale to rebuild)")
            return 0
        t = briefmod.run(cfg, conn, day, now)
    reviews = sum(1 for r in t["reviews"] if not r["stale"])
    summary = f"{len(t['events'])} meetings · {reviews} reviews · {len(t['jira'])} Jira"
    print(f"brief written to {vault.note_path(cfg, 'daily', day)}\n{t['headline']}\n{summary}")
    for err in t["errors"]:
        print(f"  ! {err}", file=sys.stderr)
    if args.scheduled:
        with closing(db.connect(cfg.db_path)) as conn:
            notify.send(cfg, conn, "brief", "Brief ready", f"{t['headline']}\n{summary}"[:240], tags=["sunrise"],
                        click=agents.obsidian_url(cfg, vault.note_path(cfg, "daily", day)), once_per_day=True,
                        also_desktop=True)
    return 0


def cmd_today(args) -> int:
    cfg = load()
    data = todaymod.load(cfg)
    if args.json:
        print(json.dumps(data or {"date": date.today().isoformat(), "missing": True}, indent=1))
        return 0
    if not data:
        print("No brief yet today. Run `me brief`.")
        return 1
    path = vault.note_path(cfg, "daily", date.today())
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    print(vault.read_section(text, "brief") or briefmod.render(data, cfg.section("jira").get("site")))
    plan = vault.read_section(text, "plan")
    if plan:
        print("\n## Plan\n" + plan)
    return 0


def cmd_plan(args) -> int:
    if not args.commit:
        return launch_ritual("plan")
    cfg = load()
    with closing(db.connect(cfg.db_path)) as conn:
        path = daymod.commit_plan(cfg, conn, parse_day(args.date), read_json_stdin())
    print(f"plan written to {path}")
    return 0


def cmd_shutdown(args) -> int:
    if not (args.actuals or args.commit):
        return launch_ritual("shutdown")
    cfg = load()
    day = parse_day(args.date)
    actual = daymod.actuals(cfg, day, todaymod.load(cfg, day))
    if args.actuals:
        print(json.dumps(actual, indent=1))
        return 0
    with closing(db.connect(cfg.db_path)) as conn:
        for path in daymod.commit_shutdown(cfg, conn, day, read_json_stdin(), actual):
            print(f"wrote {path}")
    return 0


def cmd_sheets(args) -> int:
    cfg = load()
    with closing(db.connect(cfg.db_path)) as conn:
        if args.sheets_cmd == "init":
            out = sheets.init(cfg, conn)
            verb = "created" if out["created"] else "already configured"
            print(f"{verb}:\n  Work Log      {sheets.url(out['worklog_id'])}\n  Goals & OKRs  {sheets.url(out['okr_id'])}")
        elif args.sheets_cmd == "sync":
            out = sheets.sync(cfg, conn)
            print(f"{out['status']}: {out['detail']}")
        else:
            s = cfg.section("sheets")
            for key in ("worklog_id", "okr_id"):
                print(f"{key:<11} {sheets.url(s[key]) if s.get(key) else 'not set'}")
            rows = sheets.pending(conn)
            print(f"pending     {len(rows)} day(s) not yet in the Work Log sheet")
    return 0


def cmd_schedule(args) -> int:
    cfg = load()
    fn = {"install": schedule.install, "uninstall": schedule.uninstall, "status": schedule.status}[args.schedule_cmd]
    for line in fn(cfg) or ["nothing installed"]:
        print(line)
    return 0


def cmd_pause(args) -> int:
    cfg = load()
    with closing(db.connect(cfg.db_path)) as conn:
        db.set_cursor(conn, "paused", "1" if args.command == "pause" else "0")
    print("scheduled jobs paused" if args.command == "pause" else "scheduled jobs resumed")
    return 0


def cmd_nudge(args) -> int:
    cfg = load()
    day = date.today()
    with closing(db.connect(cfg.db_path)) as conn:
        if args.scheduled and (reason := skip_scheduled(cfg, conn, day)):
            print(f"skipped: {reason}")
            return 0
        _, actual = db.get_worklog(conn, day.isoformat())
        if actual.get("at"):
            print("shutdown already done today")
            return 0
        done, total = daymod.plan_progress(cfg, day)
        progress = f"Top 3: {done}/{total} done. " if total else ""
        outcome = notify.send(cfg, conn, "shutdown", "Time to wrap up", f"{progress}Run `me shutdown` to close the day.",
                              tags=["checkered_flag"], once_per_day=True, also_desktop=True)
    print(f"nudge: {outcome}")
    return 0


def in_work_hours(cfg, now: datetime) -> bool:
    start, end = cfg.section("schedule").get("work_hours", "09:00-18:30").split("-")
    return start <= now.strftime("%H:%M") < end


def push_new_approvals(cfg, conn) -> None:
    pending = proposals.listing(conn, "pending")
    told = set(json.loads(db.get_cursor(conn, "approvals.notified") or "[]"))
    new = [p for p in pending if p["id"] not in told]
    if not new:
        return
    if len(pending) == 1:
        p = pending[0]
        actions = [notify.button(cfg, "Approve", "approve", p["id"]), notify.button(cfg, "Reject", "reject", p["id"])]
    else:
        safe = sum(1 for p in pending if p["safe"])
        actions = [notify.button(cfg, f"Approve {safe} safe", "approve_safe", 1)] if safe else []
    title = f"{len(pending)} approval{'s' if len(pending) > 1 else ''} waiting"
    notify.send(cfg, conn, "approvals", title, new[0]["preview"].splitlines()[0][:180], tags=["inbox_tray"], actions=actions)
    db.set_cursor(conn, "approvals.notified", json.dumps(sorted(told | {p["id"] for p in pending})))


def cmd_triage(args) -> int:
    cfg = load()
    now = datetime.now()
    policy = load_autonomy()
    with closing(db.connect(cfg.db_path)) as conn:
        if args.scheduled and ((reason := skip_scheduled(cfg, conn, now.date())) or not in_work_hours(cfg, now)):
            print(f"skipped: {reason or 'outside work hours'}")
            return 0
        which = ["github", "jira", "calendar"] if args.which == "all" else [args.which]
        fns = {"github": agents.triage_github, "jira": agents.triage_jira, "calendar": agents.triage_calendar}
        for name in which:
            try:
                print(json.dumps(fns[name](cfg, conn, policy)))
            except Exception as e:  # noqa: BLE001 - one agent failing must not stop the others
                print(f"{name}: failed: {e}", file=sys.stderr)
        push_new_approvals(cfg, conn)
    return 0


def cmd_tick(args) -> int:
    """Every 15 min: meeting prep, break nudges and check-in prompts."""
    cfg = load()
    now = datetime.now()
    with closing(db.connect(cfg.db_path)) as conn:
        if args.scheduled and db.is_paused(conn):
            print("skipped: paused")
            return 0
        work = cfg.is_workday(now.date()) and in_work_hours(cfg, now)
        if work:
            print(json.dumps(agents.prep_meetings(cfg, conn, load_autonomy(), now)))
            if minutes := wellbeing.break_due(conn, now):
                notify.send(cfg, conn, "break", "Time for a break?",
                            f"You've been at it {minutes // 60}h{minutes % 60:02d} straight. Five minutes away from the screen helps.",
                            priority=2, tags=["coffee"])
                db.set_cursor(conn, "nudge.break", now.isoformat())
        if at := wellbeing.checkin_due(cfg, conn, now):
            outcome = notify.send(cfg, conn, "checkin", "How are you doing?", "Tap your mood (1 = rough, 5 = great).",
                                  priority=2, tags=["seedling"],
                                  actions=[notify.button(cfg, str(n), "mood", n) for n in (2, 3, 4)])
            db.set_cursor(conn, f"checkin.{now.date()}.{at}", outcome)
            print(f"check-in prompt: {outcome}")
    return 0


def cmd_prep(args) -> int:
    cfg = load()
    with closing(db.connect(cfg.db_path)) as conn:
        print(json.dumps(agents.prep_meetings(cfg, conn, load_autonomy())))
    return 0


def cmd_inbox(args) -> int:
    cfg = load()
    with closing(db.connect(cfg.db_path)) as conn:
        print(json.dumps(agents.inbox_librarian(cfg, conn, load_autonomy())))
    return 0


def cmd_nightly(args) -> int:
    cfg = load()
    with closing(db.connect(cfg.db_path)) as conn:
        if args.scheduled and db.is_paused(conn):
            print("skipped: paused")
            return 0
        steps = [("inbox", lambda: agents.inbox_librarian(cfg, conn, load_autonomy())),
                 ("wellbeing", lambda: wellbeing.write_signals(cfg, conn, date.today())["load"]),
                 ("memory", lambda: memory.update(cfg, conn)),
                 ("codewiki", lambda: memory.update_code_wikis(cfg, conn))]
        for name, fn in steps:
            try:
                print(f"{name}: {json.dumps(fn(), default=str)}")
            except Exception as e:  # noqa: BLE001
                print(f"{name}: failed: {e}", file=sys.stderr)
    return 0


def cmd_review(args) -> int:
    cfg = load()
    with closing(db.connect(cfg.db_path)) as conn:
        if args.scheduled and db.is_paused(conn):
            print("skipped: paused")
            return 0
        out = reviewmod.run(cfg, conn)
    print(f"review written to {vault.note_path(cfg, 'reviews', date.today())}\nexperiment: {out['experiment']}")
    return 0


def cmd_memory(args) -> int:
    cfg = load()
    with closing(db.connect(cfg.db_path)) as conn:
        if args.memory_cmd == "update":
            print(memory.update(cfg, conn))
        elif args.memory_cmd == "setup":
            memory.ensure_setup(cfg)
            print(f"wrote {memory.vault_root(cfg) / 'openwiki' / 'INSTRUCTIONS.md'} and .openwikiignore")
        else:
            for r in memory.search(cfg, " ".join(args.query))["results"]:
                print(f"{r['path']}: {r['snippet']}")
    return 0


def cmd_wiki(args) -> int:
    cfg = load()
    with closing(db.connect(cfg.db_path)) as conn:
        for line in memory.update_code_wikis(cfg, conn) or ["all code wikis current"]:
            print(line)
    return 0


def cmd_ask(args) -> int:
    cfg = load()
    note = Path(args.note).expanduser() if args.note else None
    if args.json:
        try:
            print(json.dumps(askmod.structured(cfg, args.mode, note)))
            return 0
        except Exception as e:  # noqa: BLE001 - the plugin reads {"error"} from stdout
            print(json.dumps({"error": str(e)}))
            return 1
    question = " ".join(args.question).strip()
    if not question:
        raise ValueError("ask needs a question")

    def emit(event: dict) -> None:
        if args.stream:
            print(json.dumps(event), flush=True)

    r = askmod.stream(cfg, question, note, args.session, emit)
    if not args.stream:
        print(r.text)
    if not r.ok:
        print(r.error, file=sys.stderr)
        return 1
    return 0


def cmd_checkin(args) -> int:
    cfg = load()
    with closing(db.connect(cfg.db_path)) as conn:
        msg = wellbeing.record_checkin(cfg, conn, args.mood, args.energy, args.stress, args.note)
        load_ = wellbeing.load_score(wellbeing.signals(cfg, conn, date.today(),
                                                       meeting_min=(todaymod.load(cfg) or {}).get("meeting_min")))
    print(msg or f"checked in · today's load {load_['score']}/100 ({load_['band']})")
    return 0


def cmd_wins(args) -> int:
    cfg = load()
    with closing(db.connect(cfg.db_path)) as conn:
        for w in wellbeing.wins(conn, args.limit):
            print(f"{w['date']}  {w['text']}")
    return 0


def cmd_proposals(args) -> int:
    cfg = load()
    with closing(db.connect(cfg.db_path)) as conn:
        if args.proposals_cmd == "list":
            items = proposals.listing(conn, None if args.all else "pending")
            for p in items:
                print(f"{p['id']}  [{p['status']}{' · safe' if p['safe'] else ''}] {p['agent']}/{p['action']}: "
                      f"{p['preview'].splitlines()[0][:100]}")
            if not items:
                print("no pending proposals")
        elif args.proposals_cmd == "show":
            print(json.dumps(proposals.get(conn, args.id), indent=1))
        elif args.proposals_cmd == "approve":
            status, result = proposals.approve(cfg, conn, args.id)
            print(f"{status}: {result}")
        elif args.proposals_cmd == "reject":
            print("rejected" if proposals.reject(conn, args.id) else "not pending")
        else:
            for pid, status in proposals.approve_safe(cfg, conn).items():
                print(f"{pid}: {status}")
    return 0


def cmd_theme(args) -> int:
    from . import theme
    cfg = load()
    if args.theme_cmd == "list":
        current = theme.prefs(cfg)["name"]
        for t in theme.THEMES.values():
            print(f"{'●' if t.id == current else ' '} {t.id:<9} {t.description}")
        return 0
    if args.theme_cmd != "show":
        if args.value is None:
            raise ValueError(f"me theme {args.theme_cmd} needs a value")
        value: str | bool = args.value
        if args.theme_cmd == "adaptive":
            if value not in ("on", "off"):
                raise ValueError("me theme adaptive on|off")
            value = value == "on"
        elif args.theme_cmd == "accent" and value in ("none", "off"):
            value = ""
        cfg = theme.save_prefs(cfg, {"name" if args.theme_cmd == "set" else args.theme_cmd: value})
    with closing(db.connect(cfg.db_path)) as conn:
        snap = theme.snapshot(cfg, conn)
    p, s = snap["prefs"], snap["state"]
    pal = snap["palette"][theme.terminal_mode(p["mode"])]
    swatch = "".join(f"\x1b[48;2;{';'.join(map(str, theme.rgb(pal[k])))}m   " for k in ("bg", "surface", "accent", "accent-2")) + "\x1b[0m"
    print(f"{swatch if sys.stdout.isatty() else ''} {p['name']} · {p['mode']} · adaptive {'on' if p['adaptive'] else 'off'}"
          + (f" · accent {p['accent']}" if p["accent"] else ""))
    print(f"now: {s['label']} (load {s['load']}). {s['message']}" + ("" if p["adaptive"] else " (adaptive is off)"))
    for r in s["reasons"]:
        print(f"  • {r}")
    return 0


def cmd_tui(start: str) -> int:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("me's terminal UI needs an interactive terminal; see `me --help` for commands")
        return 1
    try:
        from . import tui
    except ImportError as e:  # Windows without the windows-curses package
        print(f"me's terminal UI can't start ({e}); reinstall with `uv tool install --editable --force .`")
        return 1
    return tui.main(start)


def cmd_serve(args) -> int:
    from . import server
    server.serve(load())
    return 0


def cmd_notify(args) -> int:
    cfg = load()
    with closing(db.connect(cfg.db_path)) as conn:
        outcome = notify.send(cfg, conn, "test", "me_harness test", "Notifications work. Buttons below are signed.",
                              priority=3, tags=["white_check_mark"], force=True,
                              actions=[notify.button(cfg, "Mood 4", "mood", 4)])
    print(f"{outcome}; subscribe to topic {cfg.section('ntfy').get('topic')} in the ntfy app")
    return 0


def cmd_statusline(args) -> int:
    try:
        cfg = load()
        with closing(db.connect(cfg.db_path)) as conn:
            paused = db.is_paused(conn)
        print(todaymod.statusline(cfg, paused=paused))
    except Exception:  # noqa: BLE001 - a status line must never crash the UI
        print("me")
    return 0


def cmd_hook(args) -> int:
    if args.name == "privacy-guard":
        return guard.hook_main()
    try:
        event = json.load(sys.stdin)
        cfg = load()
    except (ValueError, OSError):
        return 0
    cwd = Path(event.get("cwd") or os.getcwd()).resolve()
    roots = [cfg.home.resolve()] + [z.vault.resolve() for z in cfg.zones.values() if not z.public]
    if any(cwd.is_relative_to(r) for r in roots):
        json.dump({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                          "additionalContext": todaymod.session_context(cfg)}}, sys.stdout)
    return 0


def cmd_vault(args) -> int:
    cfg = load()
    if args.vault_cmd == "path":
        print(vault.note_path(cfg, args.kind, parse_day(args.date)))
    elif args.vault_cmd == "ensure":
        print(vault.ensure_note(cfg, args.kind, parse_day(args.date)))
    elif args.vault_cmd == "section":
        body = sys.stdin.read()
        print(vault.write_section(cfg, args.kind, args.name, body, parse_day(args.date)))
    elif args.vault_cmd == "read":
        path = vault.note_path(cfg, args.kind, parse_day(args.date))
        if not path.exists():
            print(f"{path} does not exist", file=sys.stderr)
            return 1
        text = path.read_text(encoding="utf-8")
        print((vault.read_section(text, args.section) or "") if args.section else text)
    elif args.vault_cmd == "guard-public":
        return guard_public(cfg, args.apply)
    return 0


def guard_public(cfg, apply: bool) -> int:
    missing = bootstrap.public_gitignore_missing(cfg)
    if not missing:
        print("public vaults already ignore every private folder")
        return 0
    for path, rules in missing.items():
        print(f"{path} would gain:")
        for rule in rules:
            print(f"  + {rule}")
        if not apply:
            continue
        if sys.stdin.isatty() and input("apply? [y/N] ").strip().lower() != "y":
            print("skipped")
            continue
        bootstrap.apply_gitignore(path, rules)
        print("applied")
    if not apply:
        print("re-run with --apply to write these rules")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="me", description="Personal chief-of-staff harness on Claude Code")
    sub = p.add_subparsers(dest="command")

    sub.add_parser("init", help="create me.toml, state db and the private vault").set_defaults(func=cmd_init)

    d = sub.add_parser("doctor", help="check every integration")
    d.add_argument("--live", action="store_true", help="also test connectors through headless claude (costs ~$0.20)")
    d.set_defaults(func=lambda a: doctor.main(a.live))

    for kind, help_text in (("note", "quick note"), ("todo", "quick todo"), ("idea", "quick idea")):
        c = sub.add_parser(kind, help=f"capture a {help_text} into the private inbox (no LLM call)")
        c.add_argument("text", nargs="+", help="text, or - to read stdin")
        c.set_defaults(func=cmd_capture)

    sub.add_parser("diary", help="open an interactive diary session").set_defaults(func=cmd_diary)

    b = sub.add_parser("brief", help="gather the day and write the morning brief")
    b.add_argument("--date")
    b.add_argument("--if-stale", action="store_true", help="skip when today's brief is under 6h old")
    b.add_argument("--scheduled", action="store_true", help="background-job mode: honour pause/workdays, notify when done")
    b.set_defaults(func=cmd_brief)

    t = sub.add_parser("today", help="print today's brief and plan")
    t.add_argument("--json", action="store_true", help="print state/today.json")
    t.set_defaults(func=cmd_today)

    pl = sub.add_parser("plan", help="open the planning session, or commit a plan (JSON on stdin)")
    pl.add_argument("--commit", action="store_true")
    pl.add_argument("--date")
    pl.set_defaults(func=cmd_plan)

    sd = sub.add_parser("shutdown", help="open the shutdown session, print actuals, or commit (JSON on stdin)")
    sd.add_argument("--actuals", action="store_true")
    sd.add_argument("--commit", action="store_true")
    sd.add_argument("--date")
    sd.set_defaults(func=cmd_shutdown)

    sh = sub.add_parser("sheets", help="Work Log and Goals/OKR spreadsheets")
    sh.add_argument("sheets_cmd", choices=["init", "sync", "status"])
    sh.set_defaults(func=cmd_sheets)

    sc = sub.add_parser("schedule", help="background jobs (launchd, systemd or Task Scheduler)")
    sc.add_argument("schedule_cmd", choices=["install", "uninstall", "status"])
    sc.set_defaults(func=cmd_schedule)

    for name in ("pause", "resume"):
        sub.add_parser(name, help=f"{name} all scheduled jobs").set_defaults(func=cmd_pause)

    n = sub.add_parser("nudge", help="send a reminder notification")
    n.add_argument("what", choices=["shutdown"])
    n.add_argument("--scheduled", action="store_true")
    n.set_defaults(func=cmd_nudge)

    sub.add_parser("statusline", help="Claude Code status line").set_defaults(func=cmd_statusline)

    v = sub.add_parser("vault", help="deterministic vault reads and writes")
    vs = v.add_subparsers(dest="vault_cmd", required=True)
    for name, help_text in (("path", "print a note's path"), ("ensure", "create a note from its template if missing")):
        c = vs.add_parser(name, help=help_text)
        c.add_argument("kind")
        c.add_argument("--date")
    c = vs.add_parser("section", help="replace a marker section with stdin")
    c.add_argument("kind")
    c.add_argument("name")
    c.add_argument("--date")
    c = vs.add_parser("read", help="print a note, or one section of it")
    c.add_argument("kind")
    c.add_argument("--section")
    c.add_argument("--date")
    c = vs.add_parser("guard-public", help="gitignore private folders in public vaults")
    c.add_argument("--apply", action="store_true")
    v.set_defaults(func=cmd_vault)

    h = sub.add_parser("hook", help="Claude Code hook entry points")
    h.add_argument("name", choices=["privacy-guard", "session-start"])
    h.set_defaults(func=cmd_hook)

    tr = sub.add_parser("triage", help="run the GitHub / Jira / Calendar agents")
    tr.add_argument("which", nargs="?", default="all", choices=["all", "github", "jira", "calendar"])
    tr.add_argument("--scheduled", action="store_true")
    tr.set_defaults(func=cmd_triage)

    tk = sub.add_parser("tick", help="every-15-min job: meeting prep, break nudges, check-in prompts")
    tk.add_argument("--scheduled", action="store_true")
    tk.set_defaults(func=cmd_tick)

    sub.add_parser("prep", help="write prep notes for meetings starting in 5-25 min").set_defaults(func=cmd_prep)
    sub.add_parser("inbox", help="summarise, tag and link new web clips").set_defaults(func=cmd_inbox)

    ni = sub.add_parser("nightly", help="nightly job: inbox, wellbeing signals, memory, code wikis")
    ni.add_argument("--scheduled", action="store_true")
    ni.set_defaults(func=cmd_nightly)

    rv = sub.add_parser("review", help="write this week's review")
    rv.add_argument("--scheduled", action="store_true")
    rv.set_defaults(func=cmd_review)

    mm = sub.add_parser("memory", help="OpenWiki memory over the private vault")
    mm.add_argument("memory_cmd", choices=["update", "setup", "search"])
    mm.add_argument("query", nargs="*")
    mm.set_defaults(func=cmd_memory)

    sub.add_parser("wiki", help="refresh OpenWiki for repos in [codewiki]").set_defaults(func=cmd_wiki)

    ak = sub.add_parser("ask", help="ask the chief of staff (read-only)")
    ak.add_argument("question", nargs="*")
    ak.add_argument("--stream", action="store_true", help="print stream-json events")
    ak.add_argument("--json", action="store_true", help="structured one-shot output (needs --mode and --note)")
    ak.add_argument("--mode", choices=list(askmod.MODES), default="summary")
    ak.add_argument("--note")
    ak.add_argument("--session")
    ak.set_defaults(func=cmd_ask)

    ci = sub.add_parser("checkin", help="log mood / energy / stress (1-5)")
    ci.add_argument("mood", nargs="?", type=int)
    ci.add_argument("--energy", type=int)
    ci.add_argument("--stress", type=int)
    ci.add_argument("--note")
    ci.set_defaults(func=cmd_checkin)

    w = sub.add_parser("wins", help="the wins jar")
    w.add_argument("--limit", type=int, default=20)
    w.set_defaults(func=cmd_wins)

    pr = sub.add_parser("proposals", help="the approval queue")
    pr.add_argument("proposals_cmd", nargs="?", default="list", choices=["list", "show", "approve", "reject", "approve-safe"])
    pr.add_argument("id", nargs="?")
    pr.add_argument("--all", action="store_true")
    pr.set_defaults(func=cmd_proposals)

    th = sub.add_parser("theme", help="colour theme for both UIs, and the adaptive accent")
    th.add_argument("theme_cmd", nargs="?", default="show", choices=["show", "list", "set", "mode", "adaptive", "accent"])
    th.add_argument("value", nargs="?")
    th.set_defaults(func=cmd_theme)

    sub.add_parser("serve", help="Mission Control server + phone reply listener").set_defaults(func=cmd_serve)
    sub.add_parser("tui", help="full-screen terminal UI (also: plain `me`)").set_defaults(func=lambda a: cmd_tui("Today"))
    sub.add_parser("setup", help="guided setup in the terminal UI").set_defaults(func=lambda a: cmd_tui("Setup"))
    sub.add_parser("notify-test", help="send a test push to your phone").set_defaults(func=cmd_notify)
    return p


def main(argv: list[str] | None = None) -> int:
    if (code := host.relaunch_utf8(argv)) is not None:
        return code
    if sys.stdout is None:  # pythonw: a Windows scheduled task
        args = sys.argv[1:] if argv is None else argv
        host.windowless(HOME / "state" / "logs" / f"{args[0] if args else 'me'}.log")
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        if sys.stdin.isatty() and sys.stdout.isatty():
            return cmd_tui("Today")
        parser.print_help()
        return 0
    try:
        return args.func(args) or 0
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    except (ValueError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
