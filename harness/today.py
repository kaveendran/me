"""state/today.json: the cached shape of the day, read by the status line,
the SessionStart hook, the plan ritual and (later) Mission Control."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from . import day as daymod
from .config import Config


def path(cfg: Config) -> Path:
    return cfg.state_dir / "today.json"


def save(cfg: Config, data: dict) -> None:
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    tmp = path(cfg).with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
    tmp.replace(path(cfg))


def load(cfg: Config, day: date | None = None) -> dict | None:
    """Today's data, or None when missing or from another day."""
    try:
        data = json.loads(path(cfg).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if data.get("date") == (day or date.today()).isoformat() else None


def is_stale(data: dict | None, now: datetime, max_hours: float = 6) -> bool:
    if not data:
        return True
    generated = datetime.fromisoformat(data["generated_at"])
    return (now - generated).total_seconds() > max_hours * 3600


def _short(text: str, n: int = 26) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def _fmt_minutes(m: int) -> str:
    return f"{m}m" if m < 60 else f"{m // 60}h{m % 60:02d}"


def next_event(data: dict, now: datetime) -> tuple[str, dict] | None:
    hhmm = now.strftime("%H:%M")
    for e in data.get("events", []):
        if e["end"] <= hhmm or e["title"].startswith(daymod.FOCUS_PREFIX):
            continue
        return ("now" if e["start"] <= hhmm else "next"), e
    return None


def statusline(cfg: Config, now: datetime | None = None, paused: bool = False) -> str:
    now = now or datetime.now()
    parts = ["⏸ paused"] if paused else []
    data = load(cfg, now.date())
    if not data:
        parts.append("no brief yet · me brief")
        return " │ ".join(parts)
    nxt = next_event(data, now)
    if nxt:
        state, e = nxt
        if state == "now":
            parts.append(f"● {_short(e['title'])} till {e['end']}")
        else:
            start = datetime.combine(now.date(), datetime.strptime(e["start"], "%H:%M").time())
            parts.append(f"⏭ {e['start']} {_short(e['title'])} ({_fmt_minutes(int((start - now).total_seconds() // 60))})")
    done, total = daymod.plan_progress(cfg, now.date())
    parts.append(f"◉ {done}/{total}" if total else "◌ no plan · me plan")
    reviews = sum(1 for r in data.get("reviews", []) if not r.get("stale"))
    if reviews:
        parts.append(f"⇄ {reviews} review{'s' if reviews > 1 else ''}")
    failing = sum(1 for p in data.get("my_prs", []) if p.get("ci") == "failing")
    if failing:
        parts.append(f"✗ CI {failing}")
    return " │ ".join(parts)


def session_context(cfg: Config, now: datetime | None = None) -> str:
    now = now or datetime.now()
    data = load(cfg, now.date())
    lines = [f"me_harness · {now.strftime('%A %d %B %Y, %H:%M')}"]
    if not data:
        lines.append("No brief yet today. `me brief` gathers it (about a minute); `/me:plan` runs the planning session.")
        return "\n".join(lines)
    lines.append(f"Headline: {data.get('headline') or '-'}")
    upcoming = [e for e in data.get("events", []) if e["end"] > now.strftime("%H:%M")]
    if upcoming:
        lines.append("Coming up: " + "; ".join(f"{e['start']} {e['title']}" for e in upcoming[:4]))
    done, total = daymod.plan_progress(cfg, now.date())
    lines.append(f"Plan: {done}/{total} of Top 3 done." if total else "No plan committed yet; suggest `/me:plan`.")
    lines.append("Full data: `me today --json`. Notes go through `me note|todo|idea`; vault writes through `me vault`.")
    return "\n".join(lines)
