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
        if not isinstance(z, dict) or not z.get("vault"):
            raise ConfigError(f"[zones.{name}] needs a vault path")
        zones[name] = Zone(name, Path(z["vault"]).expanduser(), bool(z.get("public", False)))

    routing = dict(raw.get("routing") or {})
    for kind, zone in routing.items():
        if zone not in zones:
            raise ConfigError(f"[routing] {kind} -> unknown zone {zone!r}")
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


def autonomy_level(policy: dict, agent: str, action: str) -> str:
    return policy.get(agent, {}).get(action) or policy.get("defaults", {}).get("level", "approve")
