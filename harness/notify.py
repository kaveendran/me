"""Notifications. Desktop (macOS) for now; ntfy phone push arrives in P3."""

from __future__ import annotations

import subprocess


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def desktop(title: str, message: str, subtitle: str | None = None) -> bool:
    script = f"display notification {_quote(message)} with title {_quote(title)}"
    if subtitle:
        script += f" subtitle {_quote(subtitle)}"
    try:
        return subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
