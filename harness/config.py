"""Load and validate me.toml and autonomy.toml."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

HOME = Path(os.environ.get("ME_HOME") or Path(__file__).resolve().parent.parent)

# Kinds of notes that must never be routed to a public zone.
PRIVATE_KINDS = frozenset({"daily", "diary", "inbox", "reviews", "wellbeing"})
LEVELS = frozenset({"auto", "approve", "off"})
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_RANGE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$")
_WEEKLY = re.compile(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun) ([01]\d|2[0-3]):[0-5]\d$")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Zone:
    name: str
    vault: Path
    public: bool


@dataclass(frozen=True)
class Config:
    home: Path
    zones: dict[str, Zone]
    paths: dict[str, str]
    routing: dict[str, str]
    private_dirs: tuple[str, ...]
    raw: dict

    def zone_for(self, kind: str) -> Zone:
        if kind not in self.routing:
            raise ConfigError(f"no routing for note kind {kind!r}")
        return self.zones[self.routing[kind]]

    def section(self, name: str) -> dict:
        return self.raw.get(name, {})

    @property
    def state_dir(self) -> Path:
        return self.home / "state"

    @property
    def db_path(self) -> Path:
        return self.state_dir / "me.db"

    @property
    def templates_dir(self) -> Path:
        return self.home / "templates"

    @property
    def plugin_dir(self) -> Path:
        return self.home / "plugin"

    @property
    def claude_settings(self) -> Path:
        # Passed as --settings to every interactive session `me` launches:
        # harness permissions, and memory plugins off so diary text stays local.
        return self.home / "config" / "claude-settings.json"

    @property
    def headless_settings(self) -> Path:
        # Headless runs get deny rules only: their --allowedTools list is the
        # whole permission surface, and "ask" rules would silently deny it.
        return self.home / "config" / "claude-headless.json"

    @property
    def name(self) -> str:
        """What the chief of staff calls the user: [me] name, else the GitHub login."""
        return self.section("me").get("name") or self.section("github").get("user") or "the user"

    def model(self, role: str) -> str:
        defaults = {"headless": "sonnet", "scout": "haiku", "interactive": "opus"}
        return self.section("models").get(role, defaults[role])

    def is_workday(self, day) -> bool:
        workdays = self.section("schedule").get("workdays", WEEKDAYS[:5])
        return WEEKDAYS[day.weekday()] in workdays


def load(path: Path | None = None, home: Path = HOME) -> Config:
    path = path or home / "me.toml"
    if not path.exists():
        raise ConfigError(f"{path} not found; run `me init`")
    with path.open("rb") as f:
        try:
            raw = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"{path}: {e}") from e
    return parse(raw, home)


def parse(raw: dict, home: Path = HOME) -> Config:
    zones_raw = raw.get("zones") or {}
    if not zones_raw:
        raise ConfigError("[zones] must define at least one zone")
    zones = {}
    for name, z in zones_raw.items():
        if not isinstance(z, dict):
            raise ConfigError(f"[zones.{name}] must be a table")
        if not z.get("vault"):
            continue  # an empty vault path means "not set up yet" (e.g. no public notes vault)
        zones[name] = Zone(name, Path(z["vault"]).expanduser(), bool(z.get("public", False)))
    if not any(not z.public for z in zones.values()):
        raise ConfigError("[zones] needs at least one private zone with a vault path")

    routing = {}
    for kind, zone in (raw.get("routing") or {}).items():
        if zone not in zones:
            if zone in zones_raw and kind not in PRIVATE_KINDS:
                continue  # routed to a zone that isn't set up: leave that kind unrouted
            raise ConfigError(f"[routing] {kind} -> unknown zone {zone!r}")
        routing[kind] = zone
        if kind in PRIVATE_KINDS and zones[zone].public:
            raise ConfigError(f"[routing] {kind} must route to a private zone, not {zone!r}")

    paths = dict(raw.get("paths") or {})
    for kind in routing:
        if kind != "knowledge" and kind not in paths:
            raise ConfigError(f"[paths] missing template for routed kind {kind!r}")

    _validate_schedule(raw.get("schedule") or {})

    private_dirs = tuple((raw.get("privacy") or {}).get("private_dirs", ()))
    return Config(home, zones, paths, routing, private_dirs, raw)


def _validate_schedule(s: dict) -> None:
    for key in ("brief", "shutdown", "nightly"):
        if key in s and not _TIME.match(s[key]):
            raise ConfigError(f"[schedule] {key} must be HH:MM, got {s[key]!r}")
    for key in ("work_hours", "quiet_hours"):
        if key in s and not _RANGE.match(s[key]):
            raise ConfigError(f"[schedule] {key} must be HH:MM-HH:MM, got {s[key]!r}")
    if "weekly" in s and not _WEEKLY.match(s["weekly"]):
        raise ConfigError(f"[schedule] weekly must look like 'Fri 17:00', got {s['weekly']!r}")
    for t in s.get("checkins", []):
        if not _TIME.match(t):
            raise ConfigError(f"[schedule] checkins entries must be HH:MM, got {t!r}")
    for d in s.get("workdays", []):
        if d not in WEEKDAYS:
            raise ConfigError(f"[schedule] workdays entries must be one of {', '.join(WEEKDAYS)}, got {d!r}")


def load_autonomy(path: Path | None = None, home: Path = HOME) -> dict[str, dict[str, str]]:
    path = path or home / "autonomy.toml"
    with path.open("rb") as f:
        raw = tomllib.load(f)
    defaults = raw.get("defaults", {})
    if defaults.get("level", "approve") not in LEVELS:
        raise ConfigError(f"autonomy [defaults] level must be one of {sorted(LEVELS)}")
    for agent, actions in raw.items():
        if agent == "defaults":
            continue
        for action, level in actions.items():
            if level not in LEVELS:
                raise ConfigError(f"autonomy [{agent}] {action} = {level!r}; use auto|approve|off")
    return raw


def set_value(path: Path, section: str, key: str, value: str) -> None:
    """Set a string value in one TOML section in place, keeping comments."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    in_section = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("["):
            in_section = stripped == f"[{section}]"
            continue
        if in_section and re.match(rf"^{re.escape(key)}\s*=", stripped):
            lines[i] = f'{key} = "{value}"\n'
            path.write_text("".join(lines), encoding="utf-8")
            return
    raise ConfigError(f"{path}: no {key} in [{section}]")


def autonomy_level(policy: dict, agent: str, action: str) -> str:
    return policy.get(agent, {}).get(action) or policy.get("defaults", {}).get("level", "approve")
