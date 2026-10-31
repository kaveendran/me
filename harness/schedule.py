"""launchd jobs for the scheduled parts of the day (macOS LaunchAgents)."""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
from pathlib import Path

from .config import WEEKDAYS, Config

PREFIX = "me.harness."
AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
# launchd numbers weekdays from Sunday = 0.
_LAUNCHD_DAY = {d: (i + 1) % 7 for i, d in enumerate(WEEKDAYS)}


def jobs(cfg: Config) -> dict[str, dict]:
    s = cfg.section("schedule")
    return {
        "brief": {"args": ["brief", "--scheduled"], "at": s.get("brief", "07:30")},
        "shutdown-nudge": {"args": ["nudge", "shutdown", "--scheduled"], "at": s.get("shutdown", "18:00")},
    }


def plist(cfg: Config, name: str, job: dict, me_bin: str) -> dict:
    hour, minute = (int(x) for x in job["at"].split(":"))
    workdays = cfg.section("schedule").get("workdays", WEEKDAYS[:5])
    logs = cfg.state_dir / "logs"
    path_dirs = [str(Path(me_bin).parent), "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
    claude = shutil.which("claude")
    if claude:
        path_dirs.insert(1, str(Path(claude).parent))
    return {
        "Label": PREFIX + name,
        "ProgramArguments": [me_bin, *job["args"]],
        "StartCalendarInterval": [{"Weekday": _LAUNCHD_DAY[d], "Hour": hour, "Minute": minute} for d in workdays],
        "WorkingDirectory": str(cfg.home),
        "EnvironmentVariables": {"PATH": ":".join(dict.fromkeys(path_dirs)), "HOME": str(Path.home())},
        "StandardOutPath": str(logs / f"{name}.log"),
        "StandardErrorPath": str(logs / f"{name}.log"),
    }


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def install(cfg: Config) -> list[str]:
    me_bin = shutil.which("me")
    if not me_bin:
        raise RuntimeError("`me` is not on PATH; run `uv tool install --editable .` first")
    (cfg.state_dir / "logs").mkdir(parents=True, exist_ok=True)
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    domain = f"gui/{os.getuid()}"
    done = []
    for name, job in jobs(cfg).items():
        label = PREFIX + name
        path = AGENTS_DIR / f"{label}.plist"
        with path.open("wb") as f:
            plistlib.dump(plist(cfg, name, job, me_bin), f)
        _launchctl("bootout", f"{domain}/{label}")
        r = _launchctl("bootstrap", domain, str(path))
        if r.returncode != 0:
            raise RuntimeError(f"launchctl bootstrap {label} failed: {r.stderr.strip()}")
        done.append(f"{label} at {job['at']}")
    return done


def uninstall(cfg: Config) -> list[str]:
    domain = f"gui/{os.getuid()}"
    removed = []
    for path in sorted(AGENTS_DIR.glob(f"{PREFIX}*.plist")):
        _launchctl("bootout", f"{domain}/{path.stem}")
        path.unlink()
        removed.append(path.stem)
    return removed


def status(cfg: Config) -> list[str]:
    lines = []
    for name, job in jobs(cfg).items():
        label = PREFIX + name
        r = _launchctl("print", f"gui/{os.getuid()}/{label}")
        if r.returncode != 0:
            lines.append(f"{label}: not installed")
            continue
        last = next((l.split("=", 1)[1].strip() for l in r.stdout.splitlines() if "last exit code" in l), "never run")
        lines.append(f"{label}: installed, {job['at']} on workdays, last exit {last}")
    return lines
