"""Notifications: ntfy push to the phone, a desktop notification alongside or as fallback.

Phone buttons work without exposing the computer: an action button POSTs a small
signed JSON body to a second ntfy "reply" topic, which `me serve` subscribes to.
Bodies carry only an id or a number, never content. The HMAC signature means
someone who learns the reply topic still can't forge an approval.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.error
import urllib.request
from datetime import datetime

from . import db, host
from .config import Config

DEFAULT_DAILY_CAP = 12


def desktop(title: str, message: str, subtitle: str | None = None) -> bool:
    return host.notify(title, message, subtitle)


def _secret(cfg: Config) -> bytes:
    path = cfg.state_dir / "notify_secret"
    if not path.exists():
        cfg.state_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_hex(32))
        path.chmod(0o600)
    return path.read_text().strip().encode()


def sign(cfg: Config, action: str, value) -> str:
    return hmac.new(_secret(cfg), f"{action}:{value}".encode(), hashlib.sha256).hexdigest()[:20]


def _ntfy(cfg: Config) -> dict:
    return cfg.section("ntfy")


def reply_url(cfg: Config) -> str:
    n = _ntfy(cfg)
    return f"{n.get('server', 'https://ntfy.sh').rstrip('/')}/{n['reply_topic']}"


def button(cfg: Config, label: str, action: str, value) -> dict:
    """An ntfy http action that posts a signed reply."""
    body = json.dumps({"action": action, "value": value, "sig": sign(cfg, action, value)})
    return {"action": "http", "label": label, "url": reply_url(cfg), "method": "POST", "body": body, "clear": True}


def view(label: str, url: str) -> dict:
    return {"action": "view", "label": label, "url": url, "clear": False}


def parse_reply(cfg: Config, body: str) -> tuple[str, object] | None:
    try:
        msg = json.loads(body)
        action, value, sig = msg["action"], msg["value"], msg["sig"]
    except (ValueError, KeyError, TypeError):
        return None
    if not hmac.compare_digest(str(sig), sign(cfg, action, value)):
        return None
    return action, value


def in_quiet_hours(cfg: Config, now: datetime) -> bool:
    spec = cfg.section("schedule").get("quiet_hours")
    if not spec:
        return False
    start, end = spec.split("-")
    hhmm = now.strftime("%H:%M")
    return (start <= hhmm or hhmm < end) if start > end else (start <= hhmm < end)


def _token(cfg: Config) -> str | None:
    n = _ntfy(cfg)
    token = os.environ.get(n.get("token_env", "ME_NTFY_TOKEN"))
    path = cfg.state_dir / "ntfy_token"
    if not token and path.exists():
        token = path.read_text().strip()
    return token or None


def _record(conn, kind: str, title: str, priority: int, sent: bool) -> None:
    with conn:
        conn.execute("INSERT INTO notifications (ts, kind, title, priority, sent) VALUES (?, ?, ?, ?, ?)",
                     (db.now_iso(), kind, title, priority, int(sent)))


def _count_today(conn, kind: str | None = None) -> int:
    today = datetime.now().astimezone().date().isoformat()
    sql = "SELECT COUNT(*) FROM notifications WHERE sent = 1 AND substr(ts, 1, 10) = ?"
    args: list = [today]
    if kind:
        sql += " AND kind = ?"
        args.append(kind)
    return conn.execute(sql, args).fetchone()[0]


def send(cfg: Config, conn, kind: str, title: str, message: str, *, priority: int = 3, tags=(), click: str | None = None,
         actions=(), once_per_day: bool = False, also_desktop: bool = False, force: bool = False,
         now: datetime | None = None) -> str:
    """Push one notification. Returns sent | quiet | capped | duplicate | unconfigured | failed: …
    Priority 5 or force bypasses quiet hours and the daily cap."""
    now = now or datetime.now()
    urgent = priority >= 5 or force
    if once_per_day and _count_today(conn, kind):
        return "duplicate"
    if not urgent and in_quiet_hours(cfg, now):
        _record(conn, kind, title, priority, False)
        return "quiet"
    cap = int(_ntfy(cfg).get("daily_cap", DEFAULT_DAILY_CAP))
    if not urgent and _count_today(conn) >= cap:
        _record(conn, kind, title, priority, False)
        return "capped"
    if also_desktop:
        desktop(title, message)
    n = _ntfy(cfg)
    if not n.get("topic"):
        sent = also_desktop or desktop(title, message)
        _record(conn, kind, title, priority, sent)
        return "unconfigured"
    payload = {"topic": n["topic"], "title": title, "message": message, "priority": priority, "tags": list(tags)}
    if click:
        payload["click"] = click
    if actions:
        payload["actions"] = list(actions)[:3]
    req = urllib.request.Request(n.get("server", "https://ntfy.sh").rstrip("/"), data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    if token := _token(cfg):
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            ok = 200 <= resp.status < 300
    except (urllib.error.URLError, OSError) as e:
        _record(conn, kind, title, priority, False)
        return f"failed: {e}"
    _record(conn, kind, title, priority, ok)
    return "sent" if ok else "failed: bad status"


def listen(cfg: Config, handler, stop=None) -> None:
    """Follow the reply topic forever, calling handler(action, value) for each valid signed reply."""
    from contextlib import closing

    backoff = 2
    while not (stop and stop.is_set()):
        with closing(db.connect(cfg.db_path)) as conn:
            since = db.get_cursor(conn, "ntfy.reply.since") or "10m"
        req = urllib.request.Request(f"{reply_url(cfg)}/json?since={since}")
        if token := _token(cfg):
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                backoff = 2
                for raw in resp:
                    if stop and stop.is_set():
                        return
                    try:
                        event = json.loads(raw)
                    except ValueError:
                        continue
                    if event.get("event") != "message":
                        continue
                    reply = parse_reply(cfg, event.get("message", ""))
                    if reply:
                        try:
                            handler(*reply)
                        except Exception as e:  # noqa: BLE001 - one bad reply must not kill the listener
                            print(f"reply handler failed: {e}")
                    with closing(db.connect(cfg.db_path)) as conn:
                        db.set_cursor(conn, "ntfy.reply.since", event["id"])
        except (urllib.error.URLError, OSError, TimeoutError):
            time.sleep(backoff)
            backoff = min(backoff * 2, 120)
