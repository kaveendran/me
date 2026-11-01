"""Colour themes for both UIs, plus an adaptive accent that follows your day.

There is one source of truth. Mission Control applies `palette` as CSS custom
properties, and the terminal UI maps the same colours to xterm-256. Colours are
built in OKLCH, so every theme and state keeps the same lightness steps, and
text stays readable whichever theme is picked (tests check the contrast).

Adaptive mode picks a state from your workload and how you feel: the load
score, your check-ins from the last few hours, the current work stretch, and
your work hours. When things get heavy the UI gets calmer, not louder. That
means lower saturation and no glow, and never alarm red.
"""

from __future__ import annotations

import math
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta

from . import today as todaymod
from . import wellbeing
from .config import Config, load, set_value

MODES = ("system", "dark", "light")
RECENT = timedelta(hours=6)  # check-ins older than this no longer say how you feel *now*


@dataclass(frozen=True)
class Theme:
    id: str
    label: str
    description: str
    neutral: tuple[float, float]  # hue, chroma of the greys
    accent: tuple[float, float]  # hue, chroma
    accent_2: float  # hue


@dataclass(frozen=True)
class State:
    id: str
    label: str
    message: str
    accent: tuple[float, float] | None  # None keeps the theme's own accent
    accent_2: float | None
    neutral: tuple[float, float] | None  # tint the greys as well
    calm: bool = False  # UIs drop glow and motion


THEMES = {t.id: t for t in (
    Theme("aurora", "Aurora", "Indigo and teal on deep navy", (265, 0.022), (277, 0.15), 185),
    Theme("graphite", "Graphite", "Neutral greys with a steel-blue accent", (255, 0.006), (250, 0.09), 215),
    Theme("fjord", "Fjord", "Cool slate with glacier blue", (230, 0.024), (228, 0.12), 170),
    Theme("ember", "Ember", "Warm charcoal with copper", (55, 0.014), (45, 0.13), 190),
    Theme("orchid", "Orchid", "Plum greys with orchid", (320, 0.018), (325, 0.14), 280),
)}

STATES = {s.id: s for s in (
    State("flow", "In flow", "Light load and good energy. A good window for deep work.", (165, 0.14), 200, None),
    State("steady", "Steady", "A normal day. Nothing needs you right now.", None, None, None),
    State("busy", "Busy", "A heavy day. Pace yourself and keep your breaks.", (70, 0.13), 40, None),
    State("strained", "Take it easy", "Load is high, so I've quieted things down. One thing at a time.",
          (295, 0.07), 250, (290, 0.014), calm=True),
    State("rest", "Off hours", "Off the clock. Work can wait until tomorrow.", (55, 0.08), 20, (50, 0.012), calm=True),
)}

# Lightness steps per mode. The greys take the theme's (or state's) tint.
STEPS = {
    "dark": {"bg": .16, "surface": .195, "surface-2": .225, "surface-3": .26, "border": .285, "border-strong": .35,
             "text": .95, "text-2": .85, "muted": .72, "dim": .64},
    "light": {"bg": .975, "surface": 1.0, "surface-2": .965, "surface-3": .94, "border": .91, "border-strong": .85,
              "text": .21, "text-2": .36, "muted": .49, "dim": .52},
}
ACCENT_L = {"dark": (.76, .82), "light": (.51, .45)}  # accent, hover
STATUS = {"ok": 150, "warn": 75, "bad": 25, "now": 0}  # hues
STATUS_LC = {"dark": (.80, .15), "light": (.51, .14)}
SOFT_ALPHA = {"dark": .14, "light": .11}
GLOW_ALPHA = {"dark": .12, "light": .08}


# ---------------------------------------------------------------- colour maths

def _from_lin(c: float) -> float:
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def _to_lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _lch_to_lin(l: float, c: float, h: float) -> tuple[float, float, float]:
    a, b = c * math.cos(math.radians(h)), c * math.sin(math.radians(h))
    l_ = (l + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (l - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (l - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return (4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
            -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
            -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_)


def _in_gamut(rgb) -> bool:
    return all(-1e-6 <= v <= 1 + 1e-6 for v in rgb)


def oklch(l: float, c: float, h: float) -> str:
    """Hex for an OKLCH colour, lowering chroma until it fits in sRGB."""
    rgb = _lch_to_lin(l, c, h)
    if not _in_gamut(rgb):
        lo, hi = 0.0, c
        for _ in range(18):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if _in_gamut(_lch_to_lin(l, mid, h)) else (lo, mid)
        rgb = _lch_to_lin(l, lo, h)
    return "#" + "".join(f"{round(max(0.0, min(1.0, _from_lin(v))) * 255):02x}" for v in rgb)


def rgb(hex_: str) -> tuple[int, int, int]:
    return int(hex_[1:3], 16), int(hex_[3:5], 16), int(hex_[5:7], 16)


def to_oklch(hex_: str) -> tuple[float, float, float]:
    r, g, b = (_to_lin(v / 255) for v in rgb(hex_))
    l_ = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m_ = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s_ = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    lab = (0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
           1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
           0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_)
    return lab[0], math.hypot(lab[1], lab[2]), math.degrees(math.atan2(lab[2], lab[1])) % 360


def rgba(hex_: str, alpha: float) -> str:
    r, g, b = rgb(hex_)
    return f"rgba({r}, {g}, {b}, {alpha:g})"


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio between two hex colours."""
    def lum(hex_):
        r, g, b = (_to_lin(v / 255) for v in rgb(hex_))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


# ---------------------------------------------------------------- palettes

def palette(theme: Theme, mode: str, state: State | None = None, accent: str = "") -> dict[str, str]:
    """Every colour token for one theme, mode and (optional) adaptive state.

    Keys match Mission Control's CSS custom properties (`--bg`, `--accent-soft` …)."""
    nh, nc = state.neutral if state and state.neutral else theme.neutral
    if state and state.accent:
        (ah, ac), a2 = state.accent, state.accent_2
    elif accent:
        _, ac, ah = to_oklch(accent)
        ac, a2 = min(max(ac, 0.04), 0.2), theme.accent_2
    else:
        (ah, ac), a2 = theme.accent, theme.accent_2
    out = {name: oklch(l, nc * (0.5 if name.startswith("text") else 1), nh) for name, l in STEPS[mode].items()}
    al, hl = ACCENT_L[mode]
    out["accent"] = oklch(al, ac, ah)
    out["accent-hover"] = oklch(hl, ac, ah)
    out["accent-ink"] = oklch(.17, nc, nh) if mode == "dark" else "#ffffff"
    out["accent-2"] = oklch(al, ac * 0.9, a2)
    sl, sc = STATUS_LC[mode]
    out.update({name: oklch(sl, sc, hue) for name, hue in STATUS.items()})
    out.update({f"{name}-soft": rgba(out[name], SOFT_ALPHA[mode]) for name in ("accent", "accent-2", "ok", "warn", "bad", "dim")})
    glow = GLOW_ALPHA[mode] * (0.4 if state and state.calm else 1)
    out["glow-1"] = rgba(out["accent"], round(glow, 3))
    out["glow-2"] = rgba(out["accent-2"], round(glow * 0.6, 3))
    return out


# ---------------------------------------------------------------- preferences

def prefs(cfg: Config) -> dict:
    t = cfg.section("theme")
    return {"name": t.get("name") if t.get("name") in THEMES else "aurora",
            "mode": t.get("mode") if t.get("mode") in MODES else "system",
            "adaptive": t.get("adaptive", True) is not False,
            "accent": (t.get("accent") or "").lower()}


def save_prefs(cfg: Config, data: dict) -> Config:
    """Validate and write [theme] keys into me.toml; returns the reloaded config."""
    updates: dict[str, str | bool] = {}
    if "name" in data:
        if data["name"] not in THEMES:
            raise ValueError(f"unknown theme {data['name']!r}; pick one of {', '.join(THEMES)}")
        updates["name"] = data["name"]
    if "mode" in data:
        if data["mode"] not in MODES:
            raise ValueError(f"mode must be one of {', '.join(MODES)}")
        updates["mode"] = data["mode"]
    if "adaptive" in data:
        if not isinstance(data["adaptive"], bool):
            raise ValueError("adaptive must be true or false")
        updates["adaptive"] = data["adaptive"]
    if "accent" in data:
        accent = (data["accent"] or "").strip().lower()
        if accent and not re.fullmatch(r"#[0-9a-f]{6}", accent):
            raise ValueError("accent must look like #4f6bed, or be empty for the theme's own")
        updates["accent"] = accent
    path = cfg.home / "me.toml"
    for key, value in updates.items():
        set_value(path, "theme", key, value, create=True)
    return load(path, cfg.home)


# ---------------------------------------------------------------- adaptive state

def off_hours(cfg: Config, now: datetime) -> str | None:
    if not cfg.is_workday(now.date()):
        return f"{now:%A}, not a workday"
    start, end = cfg.section("schedule").get("work_hours", "09:00-18:30").split("-")
    return None if start <= now.strftime("%H:%M") < end else f"outside work hours ({start}–{end})"


def recent_checkin(conn, now: datetime) -> dict:
    """The latest mood, energy and stress from check-ins in the last few hours."""
    out: dict = {}
    for c in reversed(wellbeing.checkins(conn, now.date())):
        if now - datetime.fromisoformat(c["ts"]).replace(tzinfo=None) > RECENT:
            break
        for key in ("mood", "energy", "stress"):
            if out.get(key) is None and c[key] is not None:
                out[key] = c[key]
    return out


def choose(load_: dict, recent: dict, streak_min: int, off: str | None) -> tuple[str, list[str]]:
    """Pick the adaptive state. How you feel outranks the clock, and the clock outranks workload."""
    reasons = list(load_.get("reasons", []))
    mood, energy, stress = (recent.get(k) for k in ("mood", "energy", "stress"))
    low = [f"latest check-in: {k} {v}/5" for k, v, bad in (("mood", mood, mood is not None and mood <= 2),
                                                          ("stress", stress, stress is not None and stress >= 4)) if bad]
    if low or load_.get("band") == "high":
        return "strained", low + reasons
    if off:
        return "rest", [off] + reasons
    if streak_min >= wellbeing.BREAK_AFTER_MIN:
        return "busy", [f"{streak_min} min without a break"] + reasons
    if load_.get("band") == "elevated":
        return "busy", reasons
    good = [f"latest check-in: {k} {v}/5" for k, v in (("mood", mood), ("energy", energy)) if v is not None and v >= 4]
    if good and load_.get("score", 0) <= 30:
        return "flow", good + reasons
    return "steady", reasons


def current(cfg: Config, conn, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    meeting_min = (todaymod.load(cfg) or {}).get("meeting_min")
    load_ = wellbeing.load_score(wellbeing.signals(cfg, conn, now.date(), meeting_min=meeting_min))
    sid, reasons = choose(load_, recent_checkin(conn, now), wellbeing.current_streak_min(now), off_hours(cfg, now))
    s = STATES[sid]
    return {"id": sid, "label": s.label, "message": s.message, "reasons": reasons, "calm": s.calm,
            "load": load_["score"], "band": load_["band"]}


def resolve(cfg: Config, live: dict, preview: str | None = None) -> State | None:
    """The state whose colours apply: a preview, else the live one when adaptive is on."""
    if preview:
        if preview not in STATES:
            raise ValueError(f"unknown state {preview!r}; one of {', '.join(STATES)}")
        return STATES[preview]
    return STATES[live["id"]] if prefs(cfg)["adaptive"] else None


def snapshot(cfg: Config, conn, preview: str | None = None, now: datetime | None = None) -> dict:
    """Everything a UI needs: prefs, the live state, the catalogue and resolved palettes."""
    p = prefs(cfg)
    live = current(cfg, conn, now)
    applied = resolve(cfg, live, preview)
    theme = THEMES[p["name"]]
    return {
        "prefs": p,
        "state": live,
        "applied": {"id": applied.id if applied else "steady", "calm": bool(applied and applied.calm),
                    "preview": bool(preview)},
        "palette": {mode: palette(theme, mode, applied, p["accent"]) for mode in ("dark", "light")},
        "themes": [{"id": t.id, "label": t.label, "description": t.description,
                    "swatches": {mode: [pal[k] for k in ("bg", "surface", "accent", "accent-2")]
                                 for mode in ("dark", "light") for pal in [palette(t, mode, accent="")]}}
                   for t in THEMES.values()],
        "states": [{"id": s.id, "label": s.label, "message": s.message, "calm": s.calm} for s in STATES.values()],
    }


# ---------------------------------------------------------------- terminal

CUBE = (0, 95, 135, 175, 215, 255)


def xterm256(hex_: str) -> int:
    """Nearest xterm-256 colour (6×6×6 cube or the grey ramp)."""
    r, g, b = rgb(hex_)
    idx = [min(range(6), key=lambda i: abs(CUBE[i] - v)) for v in (r, g, b)]
    grey = max(0, min(23, round(((r + g + b) / 3 - 8) / 10)))

    def dist(c):
        return 0.3 * (c[0] - r) ** 2 + 0.59 * (c[1] - g) ** 2 + 0.11 * (c[2] - b) ** 2
    cube = tuple(CUBE[i] for i in idx)
    gv = 8 + 10 * grey
    return 16 + 36 * idx[0] + 6 * idx[1] + idx[2] if dist(cube) <= dist((gv, gv, gv)) else 232 + grey


def basic8(hex_: str) -> int:
    """Closest of the eight basic terminal colours by hue (-1 = the terminal's own text colour)."""
    l, c, h = to_oklch(hex_)
    if c < 0.04:
        return -1
    for limit, color in ((40, 1), (110, 3), (170, 2), (230, 6), (290, 4), (345, 5), (360, 1)):
        if h < limit:
            return color
    return 1


def terminal_mode(pref: str) -> str:
    """dark or light for the terminal: the preference, else COLORFGBG, else macOS appearance."""
    if pref in ("dark", "light"):
        return pref
    bg = os.environ.get("COLORFGBG", "").split(";")[-1]
    if bg.isdigit():
        return "light" if int(bg) in (7, 15) else "dark"
    try:
        out = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"], capture_output=True, text=True, timeout=2)
        return "dark" if out.stdout.strip() == "Dark" else "light"
    except (OSError, subprocess.TimeoutExpired):
        return "dark"
