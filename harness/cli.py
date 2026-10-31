"""`me` command-line entry point."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

from . import bootstrap, db, doctor, guard, vault
from .config import HOME, ConfigError, load

RITUALS = {"diary": "/me:diary"}


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


def cmd_ritual(args) -> int:
    cfg = load()
    claude_bin = shutil.which("claude")
    if not claude_bin:
        print("claude CLI not found on PATH", file=sys.stderr)
        return 1
    argv = [claude_bin]
    if not plugin_installed():
        argv += ["--plugin-dir", str(cfg.plugin_dir)]
    argv.append(RITUALS[args.command])
    os.chdir(cfg.home)
    os.execv(claude_bin, argv)


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
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create me.toml, state db and the private vault").set_defaults(func=cmd_init)

    d = sub.add_parser("doctor", help="check every integration")
    d.add_argument("--live", action="store_true", help="also test connectors through headless claude (costs ~$0.20)")
    d.set_defaults(func=lambda a: doctor.main(a.live))

    for kind, help_text in (("note", "quick note"), ("todo", "quick todo"), ("idea", "quick idea")):
        c = sub.add_parser(kind, help=f"capture a {help_text} into the private inbox (no LLM call)")
        c.add_argument("text", nargs="+", help="text, or - to read stdin")
        c.set_defaults(func=cmd_capture)

    for name in RITUALS:
        sub.add_parser(name, help=f"open an interactive {name} session in the harness").set_defaults(func=cmd_ritual)

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
    h.add_argument("name", choices=["privacy-guard"])
    h.set_defaults(func=lambda a: guard.hook_main())
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args) or 0
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
