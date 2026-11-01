"""Setup: detect, check and fix every integration.

One engine behind both `me setup` (the terminal UI) and Mission Control's Setup
screen. Each check is an Item with a status and, when something can be fixed,
an action. There are no logins of our own: Claude, the claude.ai connectors and
GitHub each keep their own sign-in, and this module only finds out whether those
are in place and offers the command that fixes them.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path

from . import bootstrap, claude, db, host, memory, notify, schedule, sheets
from .config import HOME, Config, ConfigError, load, set_value

CONNECTORS_URL = "https://claude.ai/settings/connectors"
CONNECTORS = (("calendar", "Google Calendar", "claude.ai Google Calendar", True),
              ("drive", "Google Drive", "claude.ai Google Drive", True),
              ("jira", "Atlassian (Jira)", "claude.ai Atlassian", True),
              ("sheets", "Google Sheets", "claude.ai Google Sheets", False))
GROUPS = ("Profile", "Accounts", "Connectors", "Vaults", "Phone", "Automation", "Extras")
TERMINAL_COMMANDS = {
    "claude_login": ["claude", "auth", "login"],
    "github_login": ["gh", "auth", "login", "--web", "--hostname", "github.com", "--git-protocol", "https"],
}


@dataclass
class Action:
    id: str
    label: str
    kind: str  # run | confirm | terminal | link
    confirm: str | None = None
    url: str | None = None


@dataclass
class Item:
    id: str
    group: str
    label: str
    status: str  # ok | warn | todo | fail
    detail: str
    action: Action | None = None

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- detection

def _out(*cmd: str, timeout: float = 15) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""


def claude_account() -> dict:
    code, out = _out("claude", "auth", "status")
    try:
        return json.loads(out) if code == 0 else {}
    except ValueError:
        return {}


def github_login() -> str | None:
    code, out = _out("gh", "api", "user", "--jq", ".login")
    return out if code == 0 and out else None


def detected_name() -> str:
    for cmd in (("gh", "api", "user", "--jq", ".name"), ("git", "config", "--global", "user.name"), ("id", "-F")):
        code, out = _out(*cmd)
        if code == 0 and out and out != "null":
            return out.split()[0]
    return ""


def obsidian_vaults() -> list[dict]:
    paths = set()
    for config in host.obsidian_configs():
        try:
            paths |= {host.native_path(v["path"]) for v in json.loads(config.read_text()).get("vaults", {}).values()}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    return sorted(({"path": p, "name": Path(p).name} for p in paths if Path(p).is_dir()), key=lambda v: v["name"].lower())


def connectors(cfg: Config, conn, refresh: bool = False) -> dict[str, str] | None:
    """{connector name: status}, cached; refresh probes a headless session for free."""
    if refresh:
        found = claude.mcp_servers(cfg.home, cfg.headless_settings)
        db.set_cursor(conn, "setup.connectors", json.dumps(found))
        return found
    cached = db.get_cursor(conn, "setup.connectors")
    return json.loads(cached) if cached else None


def profile(cfg: Config) -> dict:
    zones = cfg.raw.get("zones", {})
    return {
        "name": cfg.section("me").get("name", ""),
        "github_user": cfg.section("github").get("user", ""),
        "jira_site": cfg.section("jira").get("site", ""),
        "private_vault": zones.get("private", {}).get("vault", ""),
        "public_vault": zones.get("public", {}).get("vault", ""),
        "crisis_country": cfg.section("me").get("crisis_country", ""),
        "timezone": cfg.section("me").get("timezone") or _out("readlink", "/etc/localtime")[1].split("zoneinfo/")[-1],
    }


# ---------------------------------------------------------------- first run

def ensure_config(home: Path = HOME) -> Config:
    """Load me.toml, creating it (with detected defaults) on first run."""
    path, created = bootstrap.create_config(home)
    if created:
        if login := github_login():
            set_value(path, "github", "user", login)
        if name := detected_name():
            set_value(path, "me", "name", name)
    cfg = load(path, home)
    if created:
        bootstrap.scaffold_private_vaults(cfg)
    with closing(db.connect(cfg.db_path)):
        pass
    return cfg


PROFILE_FIELDS = {"name": ("me", "name"), "crisis_country": ("me", "crisis_country"),
                  "github_user": ("github", "user"), "private_vault": ("zones.private", "vault"),
                  "public_vault": ("zones.public", "vault")}


def save_profile(cfg: Config, data: dict) -> Config:
    path = cfg.home / "me.toml"
    for key, value in data.items():
        if key not in PROFILE_FIELDS or value is None:
            continue
        value = str(value).strip()
        if key == "crisis_country" and value and not re.fullmatch(r"[A-Za-z]{2}", value):
            raise ValueError("crisis_country must be a 2-letter country code")
        if key.endswith("_vault") and value and not (Path(value).is_absolute() or value.startswith("~")):
            raise ValueError(f"{key} must be an absolute path or start with ~")
        if key == "private_vault" and not value:
            raise ValueError("a private vault path is required")
        section, field_ = PROFILE_FIELDS[key]
        set_value(path, section, field_, value.upper() if key == "crisis_country" else value)
    new = load(path, cfg.home)
    bootstrap.scaffold_private_vaults(new)
    return new


# ---------------------------------------------------------------- checks

def items(cfg: Config, conn, deep: bool = False) -> list[Item]:
    out: list[Item] = []
    add = out.append
    p = profile(cfg)

    add(Item("profile", "Profile", "Your name", "ok" if p["name"] else "todo",
             f"your chief of staff calls you {p['name']}" if p["name"] else "tell me what to call you"))
    add(Item("crisis", "Profile", "Helpline country", "ok" if p["crisis_country"] else "warn",
             p["crisis_country"] or "unset: findahelpline.com is shown instead"))

    acct = claude_account()
    add(Item("claude", "Accounts", "Claude", "ok" if acct.get("loggedIn") else "fail",
             f"signed in as {acct.get('email')} ({acct.get('authMethod')})" if acct.get("loggedIn") else "not signed in",
             None if acct.get("loggedIn") else Action("claude_login", "Sign in", "terminal")))
    login = github_login()
    add(Item("github", "Accounts", "GitHub", "ok" if login else "todo",
             f"signed in as {login}" + ("" if login == p["github_user"] else " (not the configured user)") if login else "not signed in",
             None if login else Action("github_login", "Sign in", "terminal")))

    status = connectors(cfg, conn, refresh=deep)
    for cid, label, name, required in CONNECTORS:
        if status is None:
            add(Item(cid, "Connectors", label, "todo", "not checked yet: run a deep re-check",
                     Action("check_connectors", "Check", "run")))
            continue
        ok = claude.mcp_available([{"name": n, "status": s} for n, s in status.items()], name)
        state = status.get(name)
        detail = ("connected" if ok else f"{state} in claude.ai" if state else "not added in claude.ai")
        if cid == "sheets" and not ok:
            detail += ": Work Log rows wait locally"
        add(Item(cid, "Connectors", label, "ok" if ok else ("todo" if required else "warn"), detail,
                 None if ok else Action("open_connectors", "Open claude.ai connectors", "link", url=CONNECTORS_URL)))
    if p["jira_site"]:
        add(Item("jira_site", "Connectors", "Jira site", "ok", p["jira_site"]))
    else:
        add(Item("jira_site", "Connectors", "Jira site", "todo", "not discovered yet",
                 Action("discover_jira", "Discover", "run")))

    private = cfg.zone_for("daily").vault
    remotes = bootstrap.git_remotes(private)
    ready = private.is_dir() and (private / "Daily").is_dir() and (private / ".git").exists()
    add(Item("private_vault", "Vaults", "Private vault", "fail" if remotes else "ok" if ready else "todo",
             f"{private} has a git remote; it must stay local" if remotes else str(private) if ready else f"{private} not set up",
             None if ready or remotes else Action("scaffold_vault", "Create", "run")))
    public = next((z for z in cfg.zones.values() if z.public), None)
    if public is None:
        add(Item("public_vault", "Vaults", "Public notes vault", "warn", "none: optional, for knowledge notes"))
    else:
        missing = bootstrap.public_gitignore_missing(cfg).get(public.vault / ".gitignore")
        add(Item("public_vault", "Vaults", "Public notes vault", "todo" if missing else "ok",
                 f"{public.vault}: private folders not gitignored" if missing else f"{public.vault}, protected",
                 Action("protect_public", "Protect", "confirm",
                        confirm=f"Add {' '.join(missing)} to {public.vault}/.gitignore so private notes can never be pushed.")
                 if missing else None))
    plugin_link = private / ".obsidian" / "plugins" / "me-harness"
    built = (cfg.home / "obsidian-plugin" / "main.js").exists()
    add(Item("obsidian_plugin", "Vaults", "Obsidian sidebar plugin", "ok" if plugin_link.exists() and built else "warn",
             "linked; enable it under Community plugins" if plugin_link.exists() and built else
             "not built: cd obsidian-plugin && npm install && npm run build" if not built else "not linked into the vault",
             Action("link_plugin", "Link", "run") if built and not plugin_link.exists() else None))

    n = cfg.section("ntfy")
    tested = conn.execute("SELECT 1 FROM notifications WHERE kind = 'test' AND sent = 1 LIMIT 1").fetchone()
    add(Item("ntfy", "Phone", "Phone notifications", "ok" if tested else "todo",
             "test push delivered" if tested else f"install ntfy, subscribe to {n.get('topic')}, then send a test",
             Action("notify_test", "Send test", "run")))

    s = cfg.section("sheets")
    have = bool(s.get("worklog_id") and s.get("okr_id"))
    add(Item("sheets_files", "Automation", "Work Log and Goals sheets", "ok" if have else "todo",
             "created in your Google Drive" if have else "not created",
             None if have else Action("create_sheets", "Create", "confirm",
                                      confirm="Create two spreadsheets in your Google Drive: me · Work Log and me · Goals & OKRs.")))
    jobs = schedule.status(cfg)
    on = sum(1 for j in jobs if "not installed" not in j)
    add(Item("schedule", "Automation", "Background jobs", "ok" if on == len(jobs) else "todo", f"{on}/{len(jobs)} {schedule.backend()} jobs",
             None if on == len(jobs) else Action("install_schedule", "Install", "confirm",
                                                 confirm=f"Install 7 {schedule.backend()} jobs: brief, triage, prep and check-ins, shutdown nudge, "
                                                         "nightly memory, weekly review, Mission Control. Expect about $1/day of "
                                                         "Claude usage. `me pause` stops them anytime.")))
    settings_ok = (cfg.home / ".claude" / "settings.json").exists()
    add(Item("project_settings", "Automation", "Project settings", "ok" if settings_ok else "todo",
             ".claude/settings.json in place" if settings_ok else "plain `claude` here would load memory plugins",
             None if settings_ok else Action("project_settings", "Install", "confirm",
                                             confirm="Copy config/claude-settings.json to .claude/settings.json: status line, "
                                                     "today's context, permissions, memory plugins off.")))

    ui = (cfg.home / "ui" / "dist" / "index.html").exists()
    add(Item("ui", "Extras", "Mission Control UI", "ok" if ui else "warn", "built" if ui else "not built",
             None if ui else Action("build_ui", "Build", "run")))
    mem = memory.initialized(memory.vault_root(cfg))
    add(Item("memory", "Extras", "Memory (OpenWiki)", "ok" if mem else "warn",
             "built" if mem else "builds tonight at 22:00",
             None if mem else Action("memory_build", "Build now", "confirm",
                                     confirm="Run OpenWiki over your private vault now. About 2 minutes and $0.50.")))
    installed = _plugin_installed()
    add(Item("user_plugin", "Extras", "/me:* in every project", "ok" if installed else "warn",
             "plugin installed for all projects" if installed else "only inside this folder",
             None if installed else Action("install_plugin", "Install", "confirm",
                                           confirm="Install the me plugin for all Claude Code projects: /me:* skills and the "
                                                   "privacy guard hook on every Write/Edit/Bash call.")))
    return out


def _plugin_installed() -> bool:
    registry = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
    try:
        return any(k.startswith("me@") for k in json.loads(registry.read_text()).get("plugins", {}))
    except (OSError, ValueError):
        return False


def complete(found: list[Item]) -> bool:
    return not any(i.status in ("todo", "fail") for i in found)


def snapshot(cfg: Config, conn, deep: bool = False) -> dict:
    found = items(cfg, conn, deep)
    n = cfg.section("ntfy")
    return {"complete": complete(found), "profile": profile(cfg), "vaults": obsidian_vaults(),
            "ntfy": {"topic": n.get("topic", ""), "subscribe_url": f"{n.get('server', 'https://ntfy.sh').rstrip('/')}/{n.get('topic', '')}"},
            "items": [i.to_dict() for i in found]}


# ---------------------------------------------------------------- actions

JIRA_SCHEMA = {"type": "object", "properties": {"sites": {"type": "array", "items": {"type": "object", "properties": {
    "id": {"type": "string"}, "url": {"type": "string"}}, "required": ["id", "url"]}}}, "required": ["sites"]}


def _discover_jira(cfg: Config, conn) -> str:
    r = claude.run("Call getAccessibleAtlassianResources and return every site in the schema. Call nothing else.",
                   cwd=cfg.home, model=cfg.model("scout"), max_turns=4, json_schema=JIRA_SCHEMA,
                   allowed_tools=("mcp__claude_ai_Atlassian__getAccessibleAtlassianResources",),
                   settings=cfg.headless_settings, persist=False, timeout=120)
    sites = (r.structured or {}).get("sites", []) if r.ok else []
    if not sites:
        raise RuntimeError(r.error or "no Atlassian sites found; add the Atlassian connector first")
    site = sites[0]
    host = re.sub(r"^https?://", "", site["url"]).rstrip("/")
    set_value(cfg.home / "me.toml", "jira", "site", host)
    set_value(cfg.home / "me.toml", "jira", "cloud_id", site["id"])
    return f"using {host}" + (f" (also found {len(sites) - 1} more; edit me.toml to switch)" if len(sites) > 1 else "")


def _project_settings(cfg: Config, conn) -> str:
    target = cfg.home / ".claude" / "settings.json"
    if target.exists():
        return ".claude/settings.json already exists; left as is"
    target.parent.mkdir(exist_ok=True)
    shutil.copyfile(cfg.claude_settings, target)
    return "installed .claude/settings.json"


def _link_plugin(cfg: Config, conn) -> str:
    link = cfg.zone_for("daily").vault / ".obsidian" / "plugins" / "me-harness"
    link.parent.mkdir(parents=True, exist_ok=True)
    if not link.exists():
        host.link_dir(link, cfg.home / "obsidian-plugin")
    return "linked; in Obsidian: Settings → Community plugins → enable me harness"


def _shell(cmd: list[str], cwd: Path, timeout: float) -> str:
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout).strip()[-400:])
    return (p.stdout.strip().splitlines() or ["done"])[-1][:200]


def _install_plugin(cfg: Config, conn) -> str:
    _shell(["claude", "plugin", "marketplace", "add", str(cfg.home)], cfg.home, 120)
    return _shell(["claude", "plugin", "install", "me@me-local"], cfg.home, 120)


def _protect_public(cfg: Config) -> str:
    done = []
    for path, rules in bootstrap.public_gitignore_missing(cfg).items():
        bootstrap.apply_gitignore(path, rules)
        done.append(f"protected {path.parent}")
    return "; ".join(done) or "already protected"


def _notify_test(cfg: Config, conn) -> str:
    out = notify.send(cfg, conn, "test", "me_harness test", "Notifications work. Buttons below are signed.",
                      tags=["white_check_mark"], force=True, actions=[notify.button(cfg, "Mood 4", "mood", 4)])
    if out != "sent":
        raise RuntimeError(f"push {out}")
    return "sent: check your phone"


ACTIONS = {
    "check_connectors": lambda cfg, conn: f"{sum(1 for s in connectors(cfg, conn, refresh=True).values() if s not in claude.UNAVAILABLE)} servers available",
    "discover_jira": _discover_jira,
    "scaffold_vault": lambda cfg, conn: "; ".join(bootstrap.scaffold_private_vaults(cfg)) or "done",
    "protect_public": lambda cfg, conn: _protect_public(cfg),
    "link_plugin": _link_plugin,
    "notify_test": _notify_test,
    "create_sheets": lambda cfg, conn: "created: " + ", ".join(sheets.url(v) for k, v in sheets.init(cfg, conn).items() if k.endswith("_id")),
    "install_schedule": lambda cfg, conn: f"installed {len(schedule.install(cfg))} jobs",
    "project_settings": _project_settings,
    "build_ui": lambda cfg, conn: (_shell([host.exe("npm"), "install"], cfg.home / "ui", 600),
                                   _shell([host.exe("npm"), "run", "build"], cfg.home / "ui", 600))[1],
    "memory_build": lambda cfg, conn: memory.update(cfg, conn),
    "install_plugin": _install_plugin,
}


def run_action(cfg: Config, conn, action_id: str, interactive: bool = False) -> dict:
    """Run one setup action. Terminal actions run in place when interactive (the TUI
    suspends itself first), otherwise in a new terminal window when there is one."""
    if action_id in TERMINAL_COMMANDS:
        cmd = TERMINAL_COMMANDS[action_id]
        if interactive:
            code = subprocess.call(cmd)
            return {"ok": code == 0, "detail": "signed in" if code == 0 else f"exited with {code}"}
        if host.open_terminal(cmd):
            return {"ok": True, "detail": "finish signing in in the terminal window that just opened"}
        return {"ok": False, "detail": f"run `{' '.join(cmd)}` in a terminal, then check again"}
    if action_id not in ACTIONS:
        raise KeyError(action_id)
    try:
        return {"ok": True, "detail": ACTIONS[action_id](cfg, conn)}
    except (RuntimeError, ValueError, ConfigError, OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "detail": str(e)[:300]}
