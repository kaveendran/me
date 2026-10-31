"""`me serve`: Mission Control's API and UI, plus the ntfy reply listener.

Binds 127.0.0.1 only. Every /api call needs the X-Me-Token header (or ?token= for
the SSE chat), and the Host header must be on the allow list, which blocks
cross-site requests and DNS rebinding from web pages.
"""

from __future__ import annotations

import json
import mimetypes
import secrets
import subprocess
import shutil
import threading
import time
from contextlib import closing
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import ask, db, memory, notify, proposals, vault, wellbeing
from . import day as daymod
from . import today as todaymod
from .config import Config

AGENTS = {
    "brief": ("Morning brief", "07:30 on workdays", ["brief"], ("brief-synth", "scout-calendar", "scout-jira", "scout-okr")),
    "triage-github": ("GitHub triage", "every 30 min, work hours", ["triage", "github"], ("triage-github",)),
    "triage-jira": ("Jira triage", "every 30 min, work hours", ["triage", "jira"], ("triage-jira", "exec-jira")),
    "triage-calendar": ("Calendar guardian", "every 30 min, work hours", ["triage", "calendar"], ("exec-calendar",)),
    "prep": ("Meeting prep", "every 15 min, work hours", ["prep"], ("prep",)),
    "inbox": ("Inbox librarian", "nightly 22:00", ["inbox"], ("inbox",)),
    "memory": ("Memory keeper", "nightly 22:00", ["memory", "update"], ("memory",)),
    "review": ("Weekly review", "Fridays 17:00", ["review"], ("review",)),
}


def token(cfg: Config) -> str:
    path = cfg.state_dir / "server_token"
    if not path.exists():
        cfg.state_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_urlsafe(32))
        path.chmod(0o600)
    return path.read_text().strip()


def today_view(cfg: Config, conn) -> dict:
    t = todaymod.load(cfg)
    path = vault.note_path(cfg, "daily", date.today())
    plan = None
    if path.exists():
        text = vault.read_section(path.read_text(encoding="utf-8"), "plan")
        if text:
            also = daymod.tasks(text.split("**Also**", 1)[1]) if "**Also**" in text else []
            blocks = [{"start": l[2:7], "end": l[8:13], "task": l[14:]} for l in text.splitlines()
                      if len(l) > 14 and l.startswith("- ") and l[4] == ":" and l[7] == "–"]
            plan = {"top3": [{"title": x.split(" · ")[0], "done": d} for d, x in daymod.top3_lines(text)],
                    "also": [{"title": x, "done": d} for d, x in also], "blocks": blocks}
    s = wellbeing.signals(cfg, conn, date.today(), meeting_min=(t or {}).get("meeting_min"))
    base = {"date": date.today().isoformat(), "missing": t is None, "plan": plan, "load": wellbeing.load_score(s),
            "proposals_pending": len(proposals.listing(conn, "pending")), "paused": db.is_paused(conn)}
    if not t:
        return {**base, "generated_at": None, "headline": "", "events": [], "tomorrow": [], "focus_windows": [],
                "meeting_min": 0, "top3_proposed": [], "risks": [], "errors": []}
    return {**base, **{k: t.get(k) for k in ("generated_at", "headline", "events", "tomorrow", "focus_windows",
                                              "meeting_min", "risks", "errors")}, "top3_proposed": t.get("top3", [])}


def work_view(cfg: Config, conn) -> dict:
    t = todaymod.load(cfg) or {}
    site = cfg.section("jira").get("site")
    jira = [{**j, "url": f"https://{site}/browse/{j['key']}" if site else ""} for j in t.get("jira", [])]
    since = (datetime.now() - timedelta(days=7)).date().isoformat()
    diagnoses = []
    for p in proposals.listing(conn, None, 200):
        if p["action"] == "diagnose_ci_failure" and p["created"][:10] >= since:
            pl = p["payload"]
            diagnoses.append({k: pl.get(k) for k in ("pr_url", "summary", "likely_cause", "suggested_fix")} | {"at": p["created"][:16]})
    return {"reviews": t.get("reviews", []), "my_prs": t.get("my_prs", []), "issues": t.get("issues", []),
            "jira": jira, "diagnoses": diagnoses}


def agents_view(cfg: Config, conn) -> dict:
    week = (datetime.now().astimezone() - timedelta(days=7)).isoformat()
    today = date.today().isoformat()
    out = []
    for name, (label, sched, _, run_names) in AGENTS.items():
        marks = ",".join("?" * len(run_names))
        last = conn.execute(f"SELECT started, status, error FROM runs WHERE agent IN ({marks}) ORDER BY id DESC LIMIT 1",
                            run_names).fetchone()
        n, cost = conn.execute(f"SELECT COUNT(*), COALESCE(SUM(cost_usd), 0) FROM runs WHERE agent IN ({marks}) AND started >= ?",
                               (*run_names, week)).fetchone()
        out.append({"name": name, "label": label, "schedule": sched, "last_run": last["started"] if last else None,
                    "status": last["status"] if last else "never", "last_error": last["error"] if last else None,
                    "runs_7d": n, "cost_7d": round(cost, 3)})
    spend = lambda since: round(conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM runs WHERE started >= ?", (since,)).fetchone()[0], 3)
    return {"agents": out, "paused": db.is_paused(conn), "spend_7d": spend(week), "spend_today": spend(today)}


def wellbeing_view(cfg: Config, conn) -> dict:
    t = todaymod.load(cfg) or {}
    s = wellbeing.signals(cfg, conn, date.today(), meeting_min=t.get("meeting_min"))
    return {"today": wellbeing.load_score(s), "days": wellbeing.history(cfg, conn),
            "checkins_today": [{k: c[k] for k in ("ts", "mood", "energy", "stress")} for c in wellbeing.checkins(conn, date.today())],
            "wins": wellbeing.wins(conn), "experiment": db.get_cursor(conn, "experiment")}


def run_agent(cfg: Config, name: str) -> None:
    me = shutil.which("me") or "me"
    subprocess.Popen([me, *AGENTS[name][2]], cwd=cfg.home, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


_visualizer: subprocess.Popen | None = None


def visualize(cfg: Config) -> str:
    global _visualizer
    wiki = memory.vault_root(cfg) / "openwiki"
    if not wiki.exists():
        raise ValueError("memory wiki not generated yet")
    if _visualizer is None or _visualizer.poll() is not None:
        _visualizer = subprocess.Popen(["openwiki", "visualize", str(wiki), "--no-open", "--port", "4321"],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        time.sleep(1.5)
    return "http://127.0.0.1:4321"


def handle_reply(cfg: Config, action: str, value) -> None:
    with closing(db.connect(cfg.db_path)) as conn:
        if action == "approve":
            proposals.approve(cfg, conn, str(value))
        elif action == "reject":
            proposals.reject(conn, str(value))
        elif action == "approve_safe":
            proposals.approve_safe(cfg, conn)
        elif action == "mood" and isinstance(value, int):
            wellbeing.record_checkin(cfg, conn, value, source="ntfy")
        elif action == "snooze":
            db.set_cursor(conn, f"snooze.{value}", (datetime.now() + timedelta(hours=1)).isoformat())


def make_handler(cfg: Config, secret: str, hosts: set[str], dist: Path):
    class Handler(BaseHTTPRequestHandler):
        server_version = "me/1"

        def log_message(self, fmt, *args):  # keep launchd logs quiet
            pass

        def _send(self, status: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, status: int = 200) -> None:
            self._send(status, json.dumps(data, default=str).encode())

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            data = json.loads(self.rfile.read(n))
            if not isinstance(data, dict):
                raise ValueError("body must be a JSON object")
            return data

        def _guard(self, query: dict) -> bool:
            if self.headers.get("Host", "") not in hosts:
                self._json({"error": "host not allowed"}, 403)
                return False
            supplied = self.headers.get("X-Me-Token") or (query.get("token") or [""])[0]
            if not secrets.compare_digest(supplied, secret):
                self._json({"error": "bad token"}, 401)
                return False
            return True

        def do_GET(self):
            url = urlparse(self.path)
            if not url.path.startswith("/api/"):
                return self._static(url.path)
            q = parse_qs(url.query)
            if not self._guard(q):
                return
            if url.path == "/api/chat":
                return self._chat(q)
            try:
                with closing(db.connect(cfg.db_path)) as conn:
                    routes = {
                        "/api/today": lambda: today_view(cfg, conn),
                        "/api/work": lambda: work_view(cfg, conn),
                        "/api/proposals": lambda: {"proposals": [
                            {k: v for k, v in p.items() if k != "payload"}
                            for p in proposals.listing(conn, None if (q.get("status") or ["pending"])[0] == "all" else "pending")]},
                        "/api/agents": lambda: agents_view(cfg, conn),
                        "/api/wellbeing": lambda: wellbeing_view(cfg, conn),
                        "/api/memory/search": lambda: memory.search(cfg, (q.get("q") or [""])[0]),
                    }
                    if url.path not in routes:
                        return self._json({"error": "not found"}, 404)
                    self._json(routes[url.path]())
            except Exception as e:  # noqa: BLE001
                self._json({"error": str(e)}, 500)

        def do_POST(self):
            url = urlparse(self.path)
            if not self._guard(parse_qs(url.query)):
                return
            parts = url.path.strip("/").split("/")
            try:
                body = self._body()
                with closing(db.connect(cfg.db_path)) as conn:
                    if parts[:2] == ["api", "proposals"] and len(parts) == 4 and parts[3] in ("approve", "reject"):
                        if parts[3] == "reject":
                            return self._json({"ok": proposals.reject(conn, parts[2])})
                        status, result = proposals.approve(cfg, conn, parts[2])
                        return self._json({"ok": status == "executed", "status": status, "result": result})
                    if parts == ["api", "proposals", "approve-safe"]:
                        results = proposals.approve_safe(cfg, conn)
                        return self._json({"approved": list(results), "results": results})
                    if parts[:2] == ["api", "agents"] and len(parts) == 4 and parts[3] == "run" and parts[2] in AGENTS:
                        run_agent(cfg, parts[2])
                        return self._json({"ok": True})
                    if parts in (["api", "pause"], ["api", "resume"]):
                        db.set_cursor(conn, "paused", "1" if parts[1] == "pause" else "0")
                        return self._json({"paused": parts[1] == "pause"})
                    if parts == ["api", "checkin"]:
                        msg = wellbeing.record_checkin(cfg, conn, body.get("mood"), body.get("energy"), body.get("stress"),
                                                       body.get("note"), source="ui")
                        return self._json({"ok": True, **({"message": msg} if msg else {})})
                    if parts == ["api", "note"]:
                        path = vault.capture(cfg, body.get("kind", "note"), body.get("text", ""))
                        return self._json({"ok": True, "path": str(path)})
                    if parts == ["api", "memory", "visualize"]:
                        return self._json({"url": visualize(cfg)})
                self._json({"error": "not found"}, 404)
            except ValueError as e:
                self._json({"error": str(e)}, 400)
            except Exception as e:  # noqa: BLE001
                self._json({"error": str(e)}, 500)

        def _chat(self, q: dict) -> None:
            question = (q.get("q") or [""])[0].strip()
            if not question:
                return self._json({"error": "missing q"}, 400)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

            def send(data: dict) -> None:
                self.wfile.write(f"data: {json.dumps(data)}\n\n".encode())
                self.wfile.flush()

            def emit(event: dict) -> None:
                if event.get("type") == "assistant":
                    for block in (event.get("message") or {}).get("content") or []:
                        if block.get("type") == "text" and block.get("text"):
                            send({"type": "text", "text": block["text"]})
                        elif block.get("type") == "tool_use":
                            send({"type": "tool", "name": block.get("name", "")})

            try:
                r = ask.stream(cfg, question, None, (q.get("session") or [None])[0], emit)
                send({"type": "done", "session": r.session_id, "cost": r.cost_usd} if r.ok else {"type": "error", "error": r.error})
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _static(self, path: str) -> None:
            if not dist.exists():
                return self._send(503, b"UI not built: cd ui && npm install && npm run build", "text/plain")
            target = (dist / path.lstrip("/")).resolve()
            if not target.is_relative_to(dist.resolve()) or not target.is_file():
                target = dist / "index.html"  # SPA fallback
            data = target.read_bytes()
            if target.name == "index.html":
                data = data.replace(b"<!--ME_TOKEN-->", f'<meta name="me-token" content="{secret}">'.encode())
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if target.suffix == ".webmanifest":
                ctype = "application/manifest+json"
            self._send(200, data, ctype)

    return Handler


def serve(cfg: Config, listen_replies: bool = True) -> None:
    port = int(cfg.section("serve").get("port", 4777))
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}", *cfg.section("serve").get("allowed_hosts", [])}
    secret = token(cfg)
    if listen_replies and cfg.section("ntfy").get("reply_topic"):
        threading.Thread(target=notify.listen, args=(cfg, lambda a, v: handle_reply(cfg, a, v)), daemon=True).start()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(cfg, secret, hosts, cfg.home / "ui" / "dist"))
    print(f"Mission Control on http://127.0.0.1:{port}")
    httpd.serve_forever()
