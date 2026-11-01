"""Background jobs for the scheduled parts of the day.

One job list, three backends: launchd LaunchAgents on macOS, systemd user timers on
Linux (and WSL with systemd), Task Scheduler on Windows. Each runs jobs only while
you're logged in, and a calendar job missed while the machine slept runs at the
next chance.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

from . import host
from .config import WEEKDAYS, Config

PREFIX = "me.harness."
AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
UNIT_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user"
UNIT_PREFIX = "me-harness-"
TASK_FOLDER = "\\me-harness\\"
# launchd numbers weekdays from Sunday = 0.
_LAUNCHD_DAY = {d: (i + 1) % 7 for i, d in enumerate(WEEKDAYS)}
_TASK_DAY = dict(zip(WEEKDAYS, ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")))
_TASK_STATES = {0x41301: "running now", 0x41303: "never run"}  # Task Scheduler result codes


def jobs(cfg: Config) -> dict[str, dict]:
    s = cfg.section("schedule")
    weekly_day, weekly_at = s.get("weekly", "Fri 17:00").split()
    return {
        "brief": {"args": ["brief", "--scheduled"], "at": s.get("brief", "07:30")},
        "shutdown-nudge": {"args": ["nudge", "shutdown", "--scheduled"], "at": s.get("shutdown", "18:00")},
        "triage": {"args": ["triage", "all", "--scheduled"], "every": int(s.get("triage_every_min", 30)) * 60},
        "tick": {"args": ["tick", "--scheduled"], "every": int(s.get("prep_every_min", 15)) * 60},
        "nightly": {"args": ["nightly", "--scheduled"], "at": s.get("nightly", "22:00"), "days": list(WEEKDAYS)},
        "review": {"args": ["review", "--scheduled"], "at": weekly_at, "days": [weekly_day]},
        "serve": {"args": ["serve"], "keepalive": True},
    }


def describe(job: dict) -> str:
    if job.get("keepalive"):
        return "always on"
    if "every" in job:
        return f"every {job['every'] // 60} min"
    return f"{job['at']} {'daily' if len(job.get('days', [])) == 7 else ' '.join(job['days']) if job.get('days') else 'on workdays'}"


def backend() -> str:
    return {"mac": "launchd", "windows": "Task Scheduler"}.get(host.kind(), "systemd")


def _days(cfg: Config, job: dict) -> list[str]:
    return job.get("days") or cfg.section("schedule").get("workdays", WEEKDAYS[:5])


def _me() -> str:
    me_bin = shutil.which("me")
    if not me_bin:
        raise RuntimeError("`me` is not on PATH; run the installer, or `uv tool install --editable .`")
    return me_bin


def install(cfg: Config) -> list[str]:
    (cfg.state_dir / "logs").mkdir(parents=True, exist_ok=True)
    return {"launchd": _launchd_install, "systemd": _systemd_install, "Task Scheduler": _task_install}[backend()](cfg)


def uninstall(cfg: Config) -> list[str]:
    return {"launchd": _launchd_uninstall, "systemd": _systemd_uninstall, "Task Scheduler": _task_uninstall}[backend()](cfg)


def status(cfg: Config) -> list[str]:
    """One line per job; a job that isn't set up says "not installed"."""
    return {"launchd": _launchd_status, "systemd": _systemd_status, "Task Scheduler": _task_status}[backend()](cfg)


# -- launchd (macOS)

def plist(cfg: Config, name: str, job: dict, me_bin: str) -> dict:
    logs = cfg.state_dir / "logs"
    path_dirs = [str(Path(me_bin).parent), "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
    claude = shutil.which("claude")
    if claude:
        path_dirs.insert(1, str(Path(claude).parent))
    out = {
        "Label": PREFIX + name,
        "ProgramArguments": [me_bin, *job["args"]],
        "WorkingDirectory": str(cfg.home),
        "EnvironmentVariables": {"PATH": ":".join(dict.fromkeys(path_dirs)), "HOME": str(Path.home())},
        "StandardOutPath": str(logs / f"{name}.log"),
        "StandardErrorPath": str(logs / f"{name}.log"),
    }
    if job.get("keepalive"):
        out.update(RunAtLoad=True, KeepAlive=True, ThrottleInterval=30)
    elif "every" in job:
        out["StartInterval"] = job["every"]
    else:
        hour, minute = (int(x) for x in job["at"].split(":"))
        out["StartCalendarInterval"] = [{"Weekday": _LAUNCHD_DAY[d], "Hour": hour, "Minute": minute} for d in _days(cfg, job)]
    return out


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def _launchd_install(cfg: Config) -> list[str]:
    me_bin = _me()
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
        done.append(f"{label}: {describe(job)}")
    return done


def _launchd_uninstall(cfg: Config) -> list[str]:
    domain = f"gui/{os.getuid()}"
    removed = []
    for path in sorted(AGENTS_DIR.glob(f"{PREFIX}*.plist")):
        _launchctl("bootout", f"{domain}/{path.stem}")
        path.unlink()
        removed.append(path.stem)
    return removed


def _launchd_status(cfg: Config) -> list[str]:
    lines = []
    for name, job in jobs(cfg).items():
        label = PREFIX + name
        r = _launchctl("print", f"gui/{os.getuid()}/{label}")
        if r.returncode != 0:
            lines.append(f"{label}: not installed")
            continue
        last = next((l.split("=", 1)[1].strip() for l in r.stdout.splitlines() if "last exit code" in l), "never run")
        lines.append(f"{label}: installed, {describe(job)}, last exit {last}")
    return lines


# -- systemd user timers (Linux)

def _unit_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"' if any(c in value for c in ' "\\') else value


def _search_path(me_bin: str) -> str:
    """PATH for jobs: where me, claude, gh, git and node live, then the system's."""
    dirs = [str(Path(me_bin).parent)]
    for tool in ("claude", "gh", "git", "node"):
        if found := shutil.which(tool):
            dirs.append(str(Path(found).parent))
    return ":".join(dict.fromkeys([*dirs, "/usr/local/bin", "/usr/bin", "/bin"]))


def units(cfg: Config, name: str, job: dict, me_bin: str) -> dict[str, str]:
    """{file name: contents} for one job: a service, plus a timer unless it's always on."""
    log = str(cfg.state_dir / "logs" / f"{name}.log")
    service = ["[Unit]", f"Description=me_harness {name}", "", "[Service]",
               f"WorkingDirectory={_unit_quote(str(cfg.home))}",
               f"Environment={_unit_quote('PATH=' + _search_path(me_bin))}",
               f"ExecStart={_unit_quote(me_bin)} {' '.join(job['args'])}",
               f"StandardOutput=append:{log}", f"StandardError=append:{log}"]
    unit = UNIT_PREFIX + name
    if job.get("keepalive"):
        service += ["Restart=always", "RestartSec=30", "", "[Install]", "WantedBy=default.target"]
        return {f"{unit}.service": "\n".join(service) + "\n"}
    service.insert(4, "Type=oneshot")
    timer = ["[Unit]", f"Description=me_harness {name}, {describe(job)}", "", "[Timer]"]
    if "every" in job:
        timer += ["OnActiveSec=1min", f"OnUnitActiveSec={job['every'] // 60}min"]
    else:
        timer += [f"OnCalendar={','.join(_days(cfg, job))} *-*-* {job['at']}:00", "Persistent=true"]
    timer += ["", "[Install]", "WantedBy=timers.target"]
    return {f"{unit}.service": "\n".join(service) + "\n", f"{unit}.timer": "\n".join(timer) + "\n"}


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)


def _systemd_ready() -> bool:
    try:
        return _systemctl("show-environment").returncode == 0
    except OSError:
        return False


def _systemd_install(cfg: Config) -> list[str]:
    me_bin = _me()
    if not _systemd_ready():
        hint = (" In WSL, add [boot] systemd=true to /etc/wsl.conf and run `wsl --shutdown` from Windows."
                if host.kind() == "wsl" else "")
        raise RuntimeError("systemd user services aren't available in this session (`systemctl --user` fails)." + hint)
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    todo = jobs(cfg)
    written = {name: units(cfg, name, job, me_bin) for name, job in todo.items()}
    for files in written.values():
        for file, text in files.items():
            (UNIT_DIR / file).write_text(text, encoding="utf-8")
    _systemctl("daemon-reload")
    done = []
    for name, files in written.items():
        target = next((f for f in files if f.endswith(".timer")), next(iter(files)))
        r = _systemctl("enable", "--now", target)
        if r.returncode == 0 and target.endswith(".service"):
            r = _systemctl("restart", target)  # an always-on job picks up a reinstall
        if r.returncode != 0:
            raise RuntimeError(f"systemctl --user enable {target} failed: {r.stderr.strip()}")
        done.append(f"{UNIT_PREFIX}{name}: {describe(todo[name])}")
    return done


def _systemd_uninstall(cfg: Config) -> list[str]:
    removed = []
    for path in sorted(UNIT_DIR.glob(f"{UNIT_PREFIX}*")):
        if path.suffix in (".timer", ".service"):
            _systemctl("disable", "--now", path.name)
            path.unlink()
            removed.append(path.name)
    if removed:
        _systemctl("daemon-reload")
    return removed


def _systemd_status(cfg: Config) -> list[str]:
    lines = []
    for name, job in jobs(cfg).items():
        unit = UNIT_PREFIX + name
        main = f"{unit}.service" if job.get("keepalive") else f"{unit}.timer"
        if not (UNIT_DIR / main).exists():
            lines.append(f"{unit}: not installed")
            continue
        state = _systemctl("is-enabled", main).stdout.strip() or "unknown"
        if state != "enabled":
            lines.append(f"{unit}: not installed ({state})")
            continue
        show = dict(l.split("=", 1) for l in _systemctl("show", f"{unit}.service", "-p", "ExecMainStatus",
                                                         "-p", "ExecMainStartTimestamp").stdout.splitlines() if "=" in l)
        last = show.get("ExecMainStatus", "?") if show.get("ExecMainStartTimestamp") else "never run"
        lines.append(f"{unit}: installed, {describe(job)}, last exit {last}")
    return lines


# -- Task Scheduler (Windows)

def task_xml(cfg: Config, name: str, job: dict, command: str, arguments: list[str], user: str) -> str:
    """A Task Scheduler definition. Runs only while you're logged on (like launchd and
    systemd user jobs), never twice at once, and on battery too."""
    start = "2020-01-01T{}:00"
    if job.get("keepalive"):
        # Start at logon, and retry every 5 minutes: IgnoreNew makes a retry a no-op while it runs.
        triggers = (f"<LogonTrigger><UserId>{escape(user)}</UserId></LogonTrigger>"
                    f"<TimeTrigger><StartBoundary>{start.format('00:00')}</StartBoundary>"
                    "<Repetition><Interval>PT5M</Interval></Repetition></TimeTrigger>")
        limit = "PT0S"
    elif "every" in job:
        triggers = (f"<TimeTrigger><StartBoundary>{start.format('00:00')}</StartBoundary>"
                    f"<Repetition><Interval>PT{job['every'] // 60}M</Interval></Repetition></TimeTrigger>")
        limit = "PT1H"
    else:
        days = "".join(f"<{_TASK_DAY[d]}/>" for d in _days(cfg, job))
        triggers = (f"<CalendarTrigger><StartBoundary>{start.format(job['at'])}</StartBoundary>"
                    f"<ScheduleByWeek><DaysOfWeek>{days}</DaysOfWeek><WeeksInterval>1</WeeksInterval></ScheduleByWeek>"
                    "</CalendarTrigger>")
        limit = "PT3H"
    args = subprocess.list2cmdline(arguments)
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>me_harness {escape(name)}, {escape(describe(job))}</Description></RegistrationInfo>
  <Triggers>{triggers}</Triggers>
  <Principals><Principal id="Author"><UserId>{escape(user)}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>{limit}</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author"><Exec><Command>{escape(command)}</Command><Arguments>{escape(args)}</Arguments><WorkingDirectory>{escape(str(cfg.home))}</WorkingDirectory></Exec></Actions>
</Task>
"""


def _task_user() -> str:
    domain, user = os.environ.get("USERDOMAIN"), os.environ.get("USERNAME", "")
    return f"{domain}\\{user}" if domain else user


def _task_command(job: dict) -> tuple[str, list[str]]:
    """pythonw runs a job without flashing a console window every 15 minutes."""
    pythonw = host.background_python()
    if pythonw:
        return str(pythonw), ["-X", "utf8", "-m", "harness", *job["args"]]
    return _me(), job["args"]


def _schtasks(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["schtasks", *args], capture_output=True, text=True)


def _task_install(cfg: Config) -> list[str]:
    _me()
    user = _task_user()
    done = []
    with tempfile.TemporaryDirectory() as tmp:
        for name, job in jobs(cfg).items():
            command, arguments = _task_command(job)
            path = Path(tmp) / f"{name}.xml"
            path.write_text(task_xml(cfg, name, job, command, arguments, user), encoding="utf-16")
            task = TASK_FOLDER + name
            r = _schtasks("/Create", "/TN", task, "/XML", str(path), "/F")
            if r.returncode != 0:
                raise RuntimeError(f"schtasks /Create {task} failed: {(r.stderr or r.stdout).strip()}")
            if job.get("keepalive"):  # restart it, so a reinstall takes effect
                _schtasks("/End", "/TN", task)
                _schtasks("/Run", "/TN", task)
            done.append(f"{task}: {describe(job)}")
    return done


def _task_names() -> list[str]:
    r = _schtasks("/Query", "/FO", "CSV", "/NH")
    names = (line.split(",", 1)[0].strip('"') for line in r.stdout.splitlines())
    return sorted({n for n in names if n.startswith(TASK_FOLDER)})


def _task_uninstall(cfg: Config) -> list[str]:
    removed = []
    for task in _task_names():
        _schtasks("/End", "/TN", task)
        if _schtasks("/Delete", "/TN", task, "/F").returncode == 0:
            removed.append(task)
    return removed


def _task_results() -> dict[str, int]:
    """{task name: last result}. PowerShell's numbers, since schtasks' text output is localised."""
    script = (f"Get-ScheduledTask -TaskPath '{TASK_FOLDER}' -ErrorAction SilentlyContinue | "
              "ForEach-Object { '{0}|{1}' -f $_.TaskName, ($_ | Get-ScheduledTaskInfo).LastTaskResult }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                             capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return {}
    return {name: int(code) for name, _, code in (l.strip().partition("|") for l in out.splitlines())
            if code.strip().lstrip("-").isdigit()}


def _task_status(cfg: Config) -> list[str]:
    installed = {n.removeprefix(TASK_FOLDER) for n in _task_names()}
    results = _task_results()
    lines = []
    for name, job in jobs(cfg).items():
        task = TASK_FOLDER + name
        if name not in installed:
            lines.append(f"{task}: not installed")
            continue
        code = results.get(name)
        last = "unknown" if code is None else _TASK_STATES.get(code, str(code))
        lines.append(f"{task}: installed, {describe(job)}, last exit {last}")
    return lines
