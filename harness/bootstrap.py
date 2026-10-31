"""`me init`: create me.toml, state, and the private vault scaffold."""

from __future__ import annotations

import json
import secrets
import shutil
import subprocess
from pathlib import Path

from .config import Config

VAULT_DIRS = ("Daily", "Diary", "Inbox/clips", "Meetings", "People", "Projects", "Reviews", "Wellbeing", "_templates")
VAULT_GITIGNORE = ".obsidian/workspace*.json\n.obsidian/cache\n.trash/\n.DS_Store\n"


def create_config(home: Path) -> tuple[Path, bool]:
    target = home / "me.toml"
    if target.exists():
        return target, False
    text = (home / "me.example.toml").read_text(encoding="utf-8")
    text = text.replace('topic = ""               # long random string; `me init` generates one',
                        f'topic = "me-{secrets.token_urlsafe(24)}"')
    text = text.replace('reply_topic = ""', f'reply_topic = "me-reply-{secrets.token_urlsafe(24)}"')
    target.write_text(text, encoding="utf-8")
    return target, True


def scaffold_private_vaults(cfg: Config) -> list[str]:
    done = []
    for zone in cfg.zones.values():
        if zone.public:
            continue
        vault = zone.vault
        for d in VAULT_DIRS:
            (vault / d).mkdir(parents=True, exist_ok=True)
        for tpl in sorted(cfg.templates_dir.glob("*.md")):
            dest = vault / "_templates" / tpl.name
            if not dest.exists():
                shutil.copyfile(tpl, dest)
        _write_once(vault / ".gitignore", VAULT_GITIGNORE)
        _write_once(vault / ".obsidian" / "daily-notes.json",
                    json.dumps({"folder": "Daily", "format": "YYYY-MM-DD", "template": "_templates/daily"}, indent=2))
        _write_once(vault / ".obsidian" / "templates.json", json.dumps({"folder": "_templates"}, indent=2))
        if not (vault / ".git").exists():
            subprocess.run(["git", "init", "-q", str(vault)], check=True)
        done.append(f"{zone.name}: {vault}")
    return done


def _write_once(path: Path, text: str) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def git_remotes(repo: Path) -> list[str]:
    if not (repo / ".git").exists():
        return []
    out = subprocess.run(["git", "-C", str(repo), "remote"], capture_output=True, text=True).stdout
    return out.split()


def public_gitignore_missing(cfg: Config) -> dict[Path, list[str]]:
    """Per public vault, the private-dir rules its .gitignore still lacks."""
    missing = {}
    for zone in cfg.zones.values():
        if not zone.public:
            continue
        path = zone.vault / ".gitignore"
        existing = set(path.read_text(encoding="utf-8").split()) if path.exists() else set()
        rules = [f"{d}/" for d in cfg.private_dirs if f"{d}/" not in existing and f"/{d}/" not in existing]
        if rules:
            missing[path] = rules
    return missing


def apply_gitignore(path: Path, rules: list[str]) -> None:
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    sep = "" if not current or current.endswith("\n") else "\n"
    block = "# me_harness: private folders never go to this public repo\n" + "\n".join(rules) + "\n"
    path.write_text(f"{current}{sep}{block}", encoding="utf-8")
