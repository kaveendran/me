import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from harness import claude, db


class DbTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "state" / "me.db"
        self.conns = []

    def tearDown(self):
        for conn in self.conns:
            conn.close()
        self._tmp.cleanup()

    def connect(self):
        conn = db.connect(self.path)
        self.conns.append(conn)
        return conn

    def test_migrate_is_idempotent(self):
        conn = self.connect()
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], len(db.MIGRATIONS))
        conn = self.connect()
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], len(db.MIGRATIONS))

    def test_record_run_and_cursors(self):
        conn = self.connect()
        result = claude.RunResult(ok=True, cost_usd=0.21, turns=6, session_id="s1")
        run_id = db.record_run(conn, agent="brief", model="sonnet", started=db.now_iso(), result=result)
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        self.assertEqual((row["status"], row["turns"], row["cost_usd"]), ("ok", 6, 0.21))
        self.assertIsNone(db.get_cursor(conn, "brief.last"))
        db.set_cursor(conn, "brief.last", "a")
        db.set_cursor(conn, "brief.last", "b")
        self.assertEqual(db.get_cursor(conn, "brief.last"), "b")

    def test_proposal_status_is_constrained(self):
        conn = self.connect()
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO proposals (id, agent, action, payload, status, created) VALUES (?, ?, ?, ?, ?, ?)",
                ("p1", "github", "review_comment", "{}", "maybe", db.now_iso()),
            )


STREAM = [
    {"type": "system", "subtype": "init", "session_id": "abc"},
    {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "mcp__claude_ai_Google_Calendar__list_calendars"},
        {"type": "text", "text": "checking"},
    ]}},
    {"type": "result", "subtype": "success", "is_error": False, "result": "{\"ok\":true}",
     "total_cost_usd": 0.2, "num_turns": 3, "session_id": "abc", "structured_output": {"ok": True}},
]


class ClaudeTest(unittest.TestCase):
    def test_build_command(self):
        cmd = claude.build_command("hi", model="sonnet", allowed_tools=["A", "B"], max_turns=5,
                                   json_schema={"type": "object"}, plugin_dir=Path("/p"),
                                   settings=Path("/s.json"))
        self.assertEqual(cmd[:6], ["claude", "-p", "hi", "--output-format", "stream-json", "--verbose"])
        self.assertIn("A,B", cmd)
        self.assertEqual(cmd[cmd.index("--json-schema") + 1], '{"type": "object"}')
        self.assertEqual(cmd[cmd.index("--plugin-dir") + 1], "/p")
        self.assertEqual(cmd[cmd.index("--settings") + 1], "/s.json")

    def test_parse_success(self):
        seen = []
        lines = [json.dumps(e) for e in STREAM] + ["not json", ""]
        r = claude.parse_events(lines, seen.append)
        self.assertTrue(r.ok)
        self.assertEqual((r.session_id, r.turns, r.cost_usd), ("abc", 3, 0.2))
        self.assertEqual(r.structured, {"ok": True})
        self.assertEqual(r.tools_used, ["mcp__claude_ai_Google_Calendar__list_calendars"])
        self.assertEqual(len(seen), 3)

    def test_parse_error_result(self):
        r = claude.parse_events([json.dumps({"type": "result", "subtype": "error_max_turns", "is_error": True})])
        self.assertFalse(r.ok)
        self.assertEqual(r.error, "error_max_turns")

    def test_parse_without_result(self):
        r = claude.parse_events([json.dumps(STREAM[0])])
        self.assertFalse(r.ok)
        self.assertIn("no result event", r.error)


if __name__ == "__main__":
    unittest.main()
