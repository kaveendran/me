"""Mission Control server: auth, host checks, API routes, static UI with token injection."""

import json
import tempfile
import threading
import unittest
import urllib.error
from contextlib import closing
import urllib.request
from dataclasses import replace
from http.server import ThreadingHTTPServer
from pathlib import Path

from harness import db, proposals, server
from harness.config import parse

from .helpers import REPO, example_raw


class ServerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        raw = example_raw()
        raw["zones"]["private"]["vault"] = str(self.tmp / "private")
        raw["zones"]["public"]["vault"] = str(self.tmp / "public")
        (self.tmp / "private").mkdir()
        (self.tmp / "public").mkdir()
        (self.tmp / "templates").symlink_to(REPO / "templates")
        dist = self.tmp / "ui" / "dist"
        dist.mkdir(parents=True)
        (dist / "index.html").write_text("<html><head><!--ME_TOKEN--></head></html>")
        self.cfg = replace(parse(raw, REPO), home=self.tmp)
        self.secret = server.token(self.cfg)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(self.cfg, self.secret, set(), dist))
        self.port = self.httpd.server_address[1]
        self.hosts = {f"127.0.0.1:{self.port}"}
        self.httpd.RequestHandlerClass = server.make_handler(self.cfg, self.secret, self.hosts, dist)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self._tmp.cleanup()

    def call(self, path, body=None, token=True, host=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=json.dumps(body).encode() if body is not None else None,
                                     method="POST" if body is not None else "GET")
        if token:
            req.add_header("X-Me-Token", self.secret)
        if host:
            req.add_header("Host", host)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.read().decode()

    def test_auth_and_host_checks(self):
        self.assertEqual(self.call("/api/today", token=False)[0], 401)
        self.assertEqual(self.call("/api/today", host="evil.example:80")[0], 403)

    def test_today_note_proposals(self):
        status, body = self.call("/api/today")
        data = json.loads(body)
        self.assertEqual((status, data["missing"], data["proposals_pending"]), (200, True, 0))
        self.assertIn("load", data)
        status, body = self.call("/api/note", {"kind": "todo", "text": "buy milk"})
        self.assertEqual(status, 200)
        self.assertIn("- [ ] buy milk (", Path(json.loads(body)["path"]).read_text())
        with closing(db.connect(self.cfg.db_path)) as conn:
            proposals.VAULT_OPS["ok"] = lambda cfg, p: "fine"
            pid, _ = proposals.propose(self.cfg, conn, {"defaults": {"level": "approve"}}, agent="jira", action="comment",
                                       target="K-1", payload={"executor": "vault", "op": "ok"}, preview="c")
        listed = json.loads(self.call("/api/proposals")[1])["proposals"]
        self.assertEqual([p["id"] for p in listed], [pid])
        self.assertNotIn("payload", listed[0])
        self.assertEqual(json.loads(self.call(f"/api/proposals/{pid}/approve", {})[1])["status"], "executed")

    def test_checkin_crisis_and_validation(self):
        self.assertEqual(self.call("/api/checkin", {"mood": 9})[0], 400)
        body = json.loads(self.call("/api/checkin", {"mood": 1, "note": "I don't want to live"})[1])
        self.assertIn("findahelpline", body["message"])

    def test_static_injects_token_and_spa_fallback(self):
        status, html = self.call("/some/route", token=False)
        self.assertEqual(status, 200)
        self.assertIn(f'<meta name="me-token" content="{self.secret}">', html)
        self.assertEqual(self.call("/../../etc/passwd", token=False)[0], 200)  # path traversal falls back to index

    def test_pause_resume(self):
        self.assertEqual(json.loads(self.call("/api/pause", {})[1]), {"paused": True})
        self.assertTrue(json.loads(self.call("/api/agents")[1])["paused"])
        self.assertEqual(json.loads(self.call("/api/resume", {})[1]), {"paused": False})


if __name__ == "__main__":
    unittest.main()
