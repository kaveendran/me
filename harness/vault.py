"""Deterministic vault writes: note paths, templates, marker sections, capture.

The model never edits harness-owned regions directly. Skills and agents hand
text to these functions, which own formatting and keep re-runs idempotent:
a section lives between `<!-- me:<name> -->` and `<!-- /me:<name> -->`, and
replacing it leaves everything outside the markers untouched.
"""

from __future__ import annotations

import os
import re
import tempfile
from datetime import date, datetime
from pathlib import Path

from .config import Config, Zone

_SECTION_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
CAPTURE_KINDS = ("note", "todo", "idea")


def render_path(template: str, day: date) -> str:
    year, week, _ = day.isocalendar()
    return template.format(date=day.isoformat(), week=f"{year}-W{week:02d}")


def note_path(cfg: Config, kind: str, day: date | None = None) -> Path:
    zone = cfg.zone_for(kind)
    return zone.vault / render_path(cfg.paths[kind], day or date.today())


def _markers(name: str) -> tuple[str, str]:
    if not _SECTION_NAME.match(name):
        raise ValueError(f"invalid section name {name!r}: use lowercase letters, digits, dashes")
    return f"<!-- me:{name} -->", f"<!-- /me:{name} -->"


def read_section(text: str, name: str) -> str | None:
    start, end = _markers(name)
    i = text.find(start)
    j = text.find(end, i + len(start)) if i != -1 else -1
    if i == -1 or j == -1:
        return None
    return text[i + len(start):j].strip("\n")


def replace_section(text: str, name: str, body: str) -> str:
    start, end = _markers(name)
    block = f"{start}\n{body.strip(chr(10))}\n{end}" if body.strip() else f"{start}\n{end}"
    i = text.find(start)
    j = text.find(end, i + len(start)) if i != -1 else -1
    if i != -1 and j != -1:
        return text[:i] + block + text[j + len(end):]
    sep = "" if not text or text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
    return f"{text}{sep}{block}\n"


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def render_template(cfg: Config, kind: str, day: date, zone: Zone) -> str:
    # Prefer the vault's own _templates/ so Obsidian and the harness agree.
    for candidate in (zone.vault / "_templates" / f"{kind}.md", cfg.templates_dir / f"{kind}.md"):
        if candidate.exists():
            text = candidate.read_text(encoding="utf-8")
            break
    else:
        text = "# {{title}}\n"
    return (
        text.replace("{{date}}", day.isoformat())
        .replace("{{title}}", day.isoformat())
        .replace("{{time}}", datetime.now().strftime("%H:%M"))
    )


def ensure_note(cfg: Config, kind: str, day: date | None = None) -> Path:
    day = day or date.today()
    path = note_path(cfg, kind, day)
    if not path.exists():
        atomic_write(path, render_template(cfg, kind, day, cfg.zone_for(kind)))
    return path


def write_section(cfg: Config, kind: str, name: str, body: str, day: date | None = None) -> Path:
    path = ensure_note(cfg, kind, day)
    text = path.read_text(encoding="utf-8")
    atomic_write(path, replace_section(text, name, body))
    return path


def capture(cfg: Config, kind: str, text: str, now: datetime | None = None) -> Path:
    if kind not in CAPTURE_KINDS:
        raise ValueError(f"capture kind must be one of {CAPTURE_KINDS}")
    text = " ".join(text.split())
    if not text:
        raise ValueError("nothing to capture")
    now = now or datetime.now()
    hhmm = now.strftime("%H:%M")
    line = {
        "note": f"- {hhmm} {text}",
        "todo": f"- [ ] {text} ({hhmm})",
        "idea": f"- {hhmm} #idea {text}",
    }[kind]
    path = ensure_note(cfg, "inbox", now.date())
    current = path.read_text(encoding="utf-8")
    sep = "" if current.endswith("\n") else "\n"
    atomic_write(path, f"{current}{sep}{line}\n")
    return path


def frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end == -1:
        return {}
    meta = {}
    for line in text[4:end].splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() and not key.startswith(" "):
            meta[key.strip()] = value.strip().strip("'\"")
    return meta


def zone_of(cfg: Config, path: Path) -> Zone | None:
    resolved = _resolve(path)
    for zone in cfg.zones.values():
        if resolved.is_relative_to(_resolve(zone.vault)):
            return zone
    return None


def private_target_reason(cfg: Config, path: Path, content: str | None = None) -> str | None:
    """Why writing `content` to `path` would leak private material, or None."""
    zone = zone_of(cfg, path)
    if zone is None or not zone.public:
        return None
    rel = _resolve(path).relative_to(_resolve(zone.vault))
    if rel.parts and rel.parts[0] in cfg.private_dirs:
        return f"{rel.parts[0]}/ is private and {zone.name} vault ({zone.vault}) is public"
    if content is not None and frontmatter(content).get("private", "").lower() == "true":
        return f"note is marked private: true and {zone.name} vault is public"
    if content is None and path.exists():
        try:
            if frontmatter(path.read_text(encoding="utf-8")).get("private", "").lower() == "true":
                return f"{rel} is marked private: true and {zone.name} vault is public"
        except (OSError, UnicodeDecodeError):
            pass
    return None


def _resolve(path: Path) -> Path:
    return Path(os.path.realpath(Path(path).expanduser()))
