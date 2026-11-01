"""What differs between macOS, Linux (WSL included) and Windows, in one place.

Everything else in the harness is plain Python, git, Claude Code and gh, which
behave the same everywhere. Background jobs live in `schedule.py`.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
from functools import cache
from pathlib import Path

WINDOWS = os.name == "nt"
# Linux terminals and how each takes a command to run, most common first.
_TERMINALS = (("gnome-terminal", ["--"]), ("konsole", ["-e"]), ("xfce4-terminal", ["-x"]), ("kitty", []),
              ("alacritty", ["-e"]), ("wezterm", ["start", "--"]), ("x-terminal-emulator", ["-e"]), ("xterm", ["-e"]))
# PowerShell's own app id: toasts from an unregistered id are silently dropped.
_TOAST_APP = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
_TOAST = (
    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
    "$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
    "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
    "$x = $t.GetElementsByTagName('text');"
    "$x.Item(0).AppendChild($t.CreateTextNode($env:ME_TITLE)) > $null;"
    "$x.Item(1).AppendChild($t.CreateTextNode($env:ME_MESSAGE)) > $null;"
    f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{_TOAST_APP}')"
    ".Show([Windows.UI.Notifications.ToastNotification]::new($t))"
)


@cache
def kind() -> str:
    """mac | linux | wsl | windows"""
    if WINDOWS:
        return "windows"
    if sys.platform == "darwin":
        return "mac"
    try:
        if "microsoft" in Path("/proc/sys/kernel/osrelease").read_text().lower():
            return "wsl"
    except OSError:
        pass
    return "linux"


def exe(name: str) -> str:
    """A tool's full path, so Windows also finds .cmd shims such as npm and openwiki."""
    return shutil.which(name) or name


def _quiet(cmd: list[str], timeout: float = 10, env: dict | None = None) -> bool:
    try:
        return subprocess.run(cmd, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL,
                              env=env).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _detach(cmd: list[str]) -> bool:
    try:
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         **new_group())
        return True
    except OSError:
        return False


def open_url(url: str) -> bool:
    k = kind()
    if k == "windows":
        try:
            os.startfile(url)  # noqa: S606 - opens the default browser
            return True
        except OSError:
            return False
    if k == "mac":
        return _quiet(["open", url])
    if k == "wsl":
        return _detach(["wslview", url] if shutil.which("wslview") else ["explorer.exe", url])
    return _detach(["xdg-open", url])


def open_terminal(cmd: list[str]) -> bool:
    """Run an interactive command (a sign-in) in a new terminal window. False when
    there's no window to open, as over SSH or in WSL; the caller says what to run."""
    k = kind()
    if k == "mac":
        line = shlex.join(cmd).replace("\\", "\\\\").replace('"', '\\"')
        return _quiet(["osascript", "-e", 'tell application "Terminal" to activate',
                       "-e", f'tell application "Terminal" to do script "{line}"'], timeout=15)
    if k == "windows":
        try:
            subprocess.Popen(["cmd", "/k", subprocess.list2cmdline(cmd)], creationflags=subprocess.CREATE_NEW_CONSOLE)
            return True
        except OSError:
            return False
    if k == "wsl" or not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    held = ["sh", "-c", f'{shlex.join(cmd)}; printf "\\nPress Enter to close. "; read _']
    for name, flag in _TERMINALS:
        if shutil.which(name):
            return _detach([name, *flag, *held])
    return False


def notify(title: str, message: str, subtitle: str | None = None) -> bool:
    """A desktop notification. Best effort: False when the desktop has no way to show one."""
    k = kind()
    if k == "mac":
        script = f"display notification {_applescript(message)} with title {_applescript(title)}"
        if subtitle:
            script += f" subtitle {_applescript(subtitle)}"
        return _quiet(["osascript", "-e", script])
    body = f"{subtitle}\n{message}" if subtitle else message
    if k in ("windows", "wsl"):
        ps = "powershell.exe" if k == "wsl" else "powershell"
        env = {**os.environ, "ME_TITLE": title, "ME_MESSAGE": body}
        if k == "wsl":  # pass the two variables through to the Windows side
            env["WSLENV"] = ":".join(filter(None, (os.environ.get("WSLENV"), "ME_TITLE", "ME_MESSAGE")))
        return _quiet([ps, "-NoProfile", "-NonInteractive", "-Command", _TOAST], timeout=20, env=env)
    return _quiet(["notify-send", "--app-name=me", title, body])


def _applescript(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def appearance() -> str | None:
    """The desktop's dark or light mode, or None when it can't tell."""
    k = kind()
    try:
        if k == "mac":  # the key exists only in dark mode
            out = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"], capture_output=True, text=True, timeout=2)
            return "dark" if out.stdout.strip() == "Dark" else "light"
        if k == "windows":
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
                return "light" if winreg.QueryValueEx(key, "AppsUseLightTheme")[0] else "dark"
        out = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
                             capture_output=True, text=True, timeout=2)
        if out.returncode == 0:
            return "dark" if "dark" in out.stdout else "light"
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def obsidian_configs() -> list[Path]:
    """Where Obsidian keeps its list of vaults (obsidian.json), most likely first."""
    home = Path.home()
    k = kind()
    if k == "mac":
        return [home / "Library" / "Application Support" / "obsidian" / "obsidian.json"]
    if k == "windows":
        return [Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming") / "obsidian" / "obsidian.json"]
    config = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    found = [config / "obsidian" / "obsidian.json",
             home / ".var" / "app" / "md.obsidian.Obsidian" / "config" / "obsidian" / "obsidian.json",  # Flatpak
             home / "snap" / "obsidian" / "current" / ".config" / "obsidian" / "obsidian.json"]
    if k == "wsl":  # Obsidian runs on the Windows side
        found += sorted(Path("/mnt/c/Users").glob("*/AppData/Roaming/obsidian/obsidian.json"))
    return found


def native_path(path: str) -> str:
    """In WSL, a Windows path from a Windows app (C:\\Users\\me\\notes) as /mnt/c/Users/me/notes."""
    if kind() == "wsl" and re.match(r"^[A-Za-z]:[\\/]", path):
        return f"/mnt/{path[0].lower()}/" + path[3:].replace("\\", "/")
    return path


def link_dir(link: Path, target: Path) -> None:
    if WINDOWS:
        # A junction needs neither admin rights nor Developer Mode, unlike a symlink.
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
        if r.returncode != 0:
            raise OSError((r.stderr or r.stdout).strip() or f"mklink failed with {r.returncode}")
    else:
        link.symlink_to(target)


def path_forms(path: str) -> set[str]:
    """How a path can be spelled in a shell command. On Windows, Claude Code's Bash tool
    is Git Bash, so C:\\Users\\me\\notes may also appear as C:/Users/me/notes or /c/Users/me/notes."""
    forms = {path}
    if WINDOWS and re.match(r"^[A-Za-z]:[\\/]", path):
        slashed = path.replace("\\", "/")
        forms |= {slashed, f"/{path[0].lower()}{slashed[2:]}", f"/{path[0].upper()}{slashed[2:]}"}
    return forms


# -- child processes

def new_group() -> dict:
    """Popen options that give the child its own process group, so kill_group can stop
    it with everything it started, and a Ctrl-C meant for us doesn't reach it."""
    return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if WINDOWS else {"start_new_session": True}


def kill_group(proc: subprocess.Popen) -> bool:
    """Kill a new_group() child and everything it started. False when nothing was left."""
    if WINDOWS:
        if proc.poll() is not None:  # never taskkill a pid that may have been reused
            return False
        return _quiet(["taskkill", "/T", "/F", "/PID", str(proc.pid)])
    try:
        os.killpg(proc.pid, signal.SIGKILL)
        return True
    except OSError:  # group already gone, or holds a process we may not signal
        return False


def relaunch_utf8(argv: list[str] | None) -> int | None:
    """Windows reads and writes text in the ANSI code page unless Python runs in UTF-8
    mode, which garbles notes, emoji and Claude's output. Rerun once in UTF-8 mode
    (its children inherit PYTHONUTF8) and return its exit code; None when not needed."""
    if not WINDOWS or sys.flags.utf8_mode:
        return None
    args = sys.argv[1:] if argv is None else argv
    previous = signal.signal(signal.SIGINT, lambda *_: None)  # Ctrl-C is the child's to handle
    try:
        return subprocess.call([sys.executable, "-X", "utf8", "-m", "harness", *args],
                               env={**os.environ, "PYTHONUTF8": "1"})
    finally:
        signal.signal(signal.SIGINT, previous)


def windowless(log: Path) -> None:
    """Under pythonw (Windows Task Scheduler) there is no console: write output to a
    log, and keep every console program we start (claude, gh, git) from opening a window."""
    log.parent.mkdir(parents=True, exist_ok=True)
    sys.stdout = sys.stderr = open(log, "a", encoding="utf-8", buffering=1)  # noqa: SIM115 - lives for the process
    if not WINDOWS:
        return
    original = subprocess.Popen.__init__

    def init(self, *args, **kwargs):
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
        original(self, *args, **kwargs)

    subprocess.Popen.__init__ = init


def background_python() -> Path | None:
    """The windowless Python beside ours (Windows), for scheduled tasks."""
    if not WINDOWS:
        return None
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return pythonw if pythonw.exists() else None
