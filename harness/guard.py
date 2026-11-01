"""PreToolUse privacy guard: keep private material out of public vaults.

Runs as a Claude Code hook (`me hook privacy-guard`) on every Write/Edit/Bash
call, in every project, so it must be fast and must fail open: if the harness
config can't be loaded, the call is allowed.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

from . import config as config_mod
from . import host
from .config import Config
from .vault import private_target_reason, zone_of

FILE_TOOLS = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}
_GIT_PUBLISH = re.compile(r"\bgit\b(?:\s+-C\s+\S+)?\s+(add|commit|push|stash\s+push)\b")
_GIT_REMOTE = re.compile(r"\bgit\b(?:\s+-C\s+\S+)?\s+(push|remote\s+(add|set-url))\b")


def check(cfg: Config, event: dict) -> str | None:
    tool = event.get("tool_name", "")
    tool_input = event.get("tool_input") or {}
    if tool in FILE_TOOLS:
        path = tool_input.get(FILE_TOOLS[tool])
        if not path:
            return None
        content = tool_input.get("content") or tool_input.get("new_string")
        return private_target_reason(cfg, Path(path), content)
    if tool == "Bash":
        return _check_bash(cfg, tool_input.get("command") or "", Path(event.get("cwd") or os.getcwd()))
    return None


def _vault_forms(vault: Path) -> list[str]:
    forms = host.path_forms(str(vault)) | host.path_forms(os.path.realpath(vault))
    for home in host.path_forms(str(Path.home())):
        for p in list(forms):
            if p.startswith(home):
                forms |= {"~" + p[len(home):], "$HOME" + p[len(home):]}
    return sorted(forms, key=len, reverse=True)


def _git_dir_target(command: str, cwd: Path) -> Path:
    match = re.search(r"\bgit\s+-C\s+(\S+)", command)
    if match:
        target = Path(os.path.expanduser(match.group(1).strip("'\"")))
        return target if target.is_absolute() else cwd / target
    return cwd


def _check_bash(cfg: Config, command: str, cwd: Path) -> str | None:
    public = [z for z in cfg.zones.values() if z.public]
    private = [z for z in cfg.zones.values() if not z.public]

    for pz in public:
        for form in _vault_forms(pz.vault):
            for d in cfg.private_dirs:
                if f"{form}/{d}" in command or f"{form}\\{d}" in command:
                    return f"command touches {d}/ inside the public {pz.name} vault"
        if any(f in command for f in _vault_forms(pz.vault)):
            for prz in private:
                if any(f in command for f in _vault_forms(prz.vault)):
                    return (f"command references both the private {prz.name} vault and the public "
                            f"{pz.name} vault; promotions go through an approved proposal")

    git_target = _git_dir_target(command, cwd)
    zone = zone_of(cfg, git_target)
    if zone is None:
        return None
    if zone.public and _GIT_PUBLISH.search(command):
        if _names_private_dir(command, cfg.private_dirs):
            return f"git command stages a private folder in the public {zone.name} vault"
        leaked = _unignored_private_changes(zone.vault, cfg.private_dirs)
        if leaked:
            return (f"public {zone.name} vault has un-ignored private files ({', '.join(leaked[:3])}); "
                    "run `me vault guard-public` to gitignore them first")
    if not zone.public and _GIT_REMOTE.search(command):
        return f"the private {zone.name} vault must stay local (no remotes, no push)"
    return None


def _names_private_dir(command: str, private_dirs: tuple[str, ...]) -> bool:
    tokens = command.split()  # raw words too: shlex drops the backslashes of Diary\x.md
    try:
        tokens += shlex.split(command)
    except ValueError:
        pass
    return any(t.strip("'\"").replace("\\", "/").strip("/").split("/")[0] in private_dirs
               for t in tokens if not t.startswith("-"))


def _unignored_private_changes(vault: Path, private_dirs: tuple[str, ...]) -> list[str]:
    try:
        out = subprocess.run(
            ["git", "-C", str(vault), "status", "--porcelain", "--untracked-files=all"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    leaked = []
    for line in out.splitlines():
        path = line[3:].strip().strip('"')
        if path.split("/")[0] in private_dirs:
            leaked.append(path)
    return leaked


def hook_main(stdin=sys.stdin, stdout=sys.stdout) -> int:
    try:
        event = json.load(stdin)
        cfg = config_mod.load()
    except (ValueError, OSError):
        return 0
    reason = check(cfg, event)
    if reason:
        json.dump({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"me privacy guard: {reason}",
            }
        }, stdout)
    return 0
