"""Wellbeing: check-ins, passive signals, a transparent load score, gentle nudges.

Supportive, not clinical: the score is a heuristic whose reasons are always shown,
nothing here diagnoses, and no wellbeing data ever leaves the machine except
check-in button taps (a bare number) through the ntfy reply topic.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta

from . import activity, db, vault
from .config import Config

CRISIS = re.compile(r"suicid|kill myself|end it all|self[- ]?harm|hurt myself|don'?t want to (be alive|live)|"
                    r"no reason to live|better off without me", re.I)
HELPLINES = {"LK": "Sri Lanka: 1926 (National Institute of Mental Health helpline) or 1333 (CCCline)"}
BREAK_AFTER_MIN = 100
BREAK_REPEAT_MIN = 90


def crisis_message(cfg: Config) -> str:
    country = (cfg.section("me").get("crisis_country") or "").upper()
    local = f" {HELPLINES[country]}." if country in HELPLINES else ""
    return ("It sounds like things are really heavy right now. You don't have to carry this alone: please reach out "
            f"to someone you trust today, or talk to a trained listener.{local} Helplines worldwide: "
            "https://findahelpline.com. If you're in immediate danger, call your local emergency number.")


def record_checkin(cfg: Config, conn, mood: int | None, energy: int | None = None, stress: int | None = None,
                   note: str | None = None, source: str = "cli") -> str | None:
    """Store a check-in; returns a support message when the note suggests a crisis."""
    for name, value in (("mood", mood), ("energy", energy), ("stress", stress)):
        if value is not None and value not in range(1, 6):
            raise ValueError(f"{name} must be 1-5")
    if mood is None and energy is None and stress is None:
        raise ValueError("check-in needs at least one of mood, energy, stress")
    with conn:
        conn.execute("INSERT INTO checkins (ts, mood, energy, stress, note, source) VALUES (?, ?, ?, ?, ?, ?)",
                     (db.now_iso(), mood, energy, stress, note, source))
    write_day_note(cfg, conn, date.today())
    return crisis_message(cfg) if note and CRISIS.search(note) else None


def checkins(conn, day: date) -> list[dict]:
    rows = conn.execute("SELECT ts, mood, energy, stress, note, source FROM checkins WHERE substr(ts, 1, 10) = ? ORDER BY ts",
                        (day.isoformat(),))
    return [dict(r) for r in rows]


def _avg(values) -> float | None:
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 1) if values else None


def signals(cfg: Config, conn, day: date, now: datetime | None = None, meeting_min: int | None = None) -> dict:
    stamps = activity.timeline(day)
    runs = activity.streaks(stamps)
    longest = max((round((b - a).total_seconds() / 60) for a, b in runs), default=0)
    late = sum(1 for ts in stamps if ts.hour >= 23 or ts.hour < 5)
    active = round(activity._active(stamps).total_seconds() / 60) if len(stamps) > 1 else 0
    if meeting_min is None:
        _, actual = db.get_worklog(conn, day.isoformat())
        meeting_min = actual.get("meeting_min", 0)
    entries = checkins(conn, day)
    skipped = 0
    first = conn.execute("SELECT MIN(day) FROM worklog").fetchone()[0]  # don't count days before the harness existed
    for i in range(1, 8):
        d = day - timedelta(days=i)
        if not first or d.isoformat() < first:
            break
        if not cfg.is_workday(d):
            continue
        if db.get_worklog(conn, d.isoformat())[1].get("at"):
            break
        skipped += 1
    return {
        "date": day.isoformat(),
        "meeting_min": meeting_min,
        "active_min": active,
        "longest_streak_min": longest,
        "late_events": late,
        "weekend_work": day.weekday() >= 5 and active > 30,
        "mood": _avg(e["mood"] for e in entries),
        "energy": _avg(e["energy"] for e in entries),
        "stress": _avg(e["stress"] for e in entries),
        "skipped_shutdowns": skipped,
    }


def load_score(s: dict) -> dict:
    score, reasons = 0, []

    def add(points: int, reason: str) -> None:
        nonlocal score
        score += points
        reasons.append(reason)

    if s["meeting_min"] >= 300:
        add(25, f"{s['meeting_min'] / 60:.1f}h of meetings")
    elif s["meeting_min"] >= 240:
        add(12, f"{s['meeting_min'] / 60:.1f}h of meetings")
    if s["longest_streak_min"] >= 150:
        add(15, f"{s['longest_streak_min'] // 60}h{s['longest_streak_min'] % 60:02d} without a break")
    if s["active_min"] >= 540:
        add(15, f"{s['active_min'] / 60:.1f}h at the keyboard")
    if s["late_events"] >= 5:
        add(20, "working past 23:00")
    if s["weekend_work"]:
        add(10, "weekend work")
    if s["mood"] is not None and s["mood"] <= 2:
        add(20, f"mood {s['mood']}/5")
    if s["energy"] is not None and s["energy"] <= 2:
        add(10, f"energy {s['energy']}/5")
    if s["stress"] is not None and s["stress"] >= 4:
        add(10, f"stress {s['stress']}/5")
    if s["skipped_shutdowns"] >= 2:
        add(10, f"{s['skipped_shutdowns']} workdays without a shutdown")
    score = min(score, 100)
    band = "high" if score > 60 else "elevated" if score > 30 else "ok"
    return {"score": score, "band": band, "reasons": reasons}


def current_streak_min(now: datetime) -> int:
    runs = activity.streaks(activity.timeline(now.date()))
    if not runs or now - runs[-1][1] > timedelta(minutes=10):
        return 0
    return round((runs[-1][1] - runs[-1][0]).total_seconds() / 60)


def break_due(conn, now: datetime) -> int | None:
    streak = current_streak_min(now)
    if streak < BREAK_AFTER_MIN:
        return None
    last = db.get_cursor(conn, "nudge.break")
    if last and now - datetime.fromisoformat(last) < timedelta(minutes=BREAK_REPEAT_MIN):
        return None
    return streak


def checkin_due(cfg: Config, conn, now: datetime) -> str | None:
    for t in cfg.section("schedule").get("checkins", []):
        at = datetime.combine(now.date(), datetime.strptime(t, "%H:%M").time())
        if at <= now < at + timedelta(minutes=15) and not db.get_cursor(conn, f"checkin.{now.date()}.{t}"):
            return t
    return None


def wins(conn, limit: int = 20) -> list[dict]:
    out = []
    for row in conn.execute("SELECT day, actual FROM worklog WHERE actual IS NOT NULL ORDER BY day DESC LIMIT 60"):
        for w in json.loads(row["actual"]).get("wins") or []:
            out.append({"date": row["day"], "text": w})
    return out[:limit]


def write_day_note(cfg: Config, conn, day: date) -> None:
    entries = checkins(conn, day)
    lines = [f"- {e['ts'][11:16]} " + " · ".join(f"{k} {e[k]}/5" for k in ("mood", "energy", "stress") if e[k])
             for e in entries]
    vault.write_section(cfg, "wellbeing", "checkins", "\n".join(lines) or "_no check-ins yet_", day)


def write_signals(cfg: Config, conn, day: date) -> dict:
    s = signals(cfg, conn, day)
    load = load_score(s)
    body = (f"**Load** {load['score']}/100 ({load['band']})" + (": " + "; ".join(load["reasons"]) if load["reasons"] else "")
            + f"\n\nmeetings {s['meeting_min'] / 60:.1f}h · keyboard {s['active_min'] / 60:.1f}h · "
              f"longest stretch {s['longest_streak_min']}m")
    vault.write_section(cfg, "wellbeing", "signals", body, day)
    return {**s, "load": load}


def history(cfg: Config, conn, days: int = 14) -> list[dict]:
    out = []
    for i in range(days - 1, -1, -1):
        d = date.today() - timedelta(days=i)
        cached = db.get_cursor(conn, f"signals.{d}") if i else None  # past days don't change; cache them
        if cached:
            out.append(json.loads(cached))
            continue
        s = signals(cfg, conn, d)
        row = {"date": d.isoformat(), "mood": s["mood"], "energy": s["energy"], "stress": s["stress"],
               "load": load_score(s)["score"], "meeting_min": s["meeting_min"], "active_min": s["active_min"]}
        if i:
            db.set_cursor(conn, f"signals.{d}", json.dumps(row))
        out.append(row)
    return out
