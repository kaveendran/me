"""The brief end to end with every external source mocked, plus today.json views."""

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from unittest import mock

from harness import brief, claude, db, sheets, today, vault

from .helpers import make_cfg

DAY = date(2026, 11, 2)
NOW = datetime(2026, 11, 2, 7, 30)

CAL = {"events": [
    {"id": "1", "title": "Standup", "start": "2026-11-02T09:30", "end": "2026-11-02T09:45", "all_day": False,
     "response": "accepted", "attendees": 6},
    {"id": "2", "title": "Design review", "start": "2026-11-02T14:00", "end": "2026-11-02T15:00", "all_day": False,
     "response": "accepted", "attendees": 4},
    {"id": "3", "title": "Planning", "start": "2026-11-03T10:00", "end": "2026-11-03T11:00", "all_day": False,
     "response": "accepted", "attendees": 8},
], "errors": []}
JIRA = {"issues": [{"key": "PROJ-7", "summary": "Fix login", "status": "In Progress", "status_category": "In Progress"}],
        "errors": []}
GH = {"reviews": [{"repo": "a/b", "number": 3, "title": "t", "url": "https://gh/3", "author": "z", "stale": False, "updated": "", "draft": False}],
      "my_prs": [{"repo": "a/b", "number": 4, "title": "mine", "url": "https://gh/4", "stale": False, "ci": "failing", "updated": "", "draft": False, "author": None}],
      "issues": [], "errors": []}
SYNTH = {"headline": "Two meetings, one failing PR to fix first.",
         "top3": [{"title": "Fix CI on a/b#4", "why": "Blocks the release", "ref": "https://gh/4", "est_min": 45, "source": "github"}],
         "blocks": [{"start": "09:45", "end": "10:30", "task": "Fix CI"}, {"start": "14:15", "end": "14:45", "task": "bad"}],
         "risks": []}


class BriefRunTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.cfg = replace(make_cfg(self.tmp), home=self.tmp)
        (self.tmp / "templates").symlink_to(Path(__file__).resolve().parent.parent / "templates")
        self.conn = db.connect(self.cfg.db_path)

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def test_run_writes_note_and_today(self):
        synth = claude.RunResult(ok=True, structured=json.loads(json.dumps(SYNTH)), cost_usd=0.02, turns=1)
        with mock.patch("harness.scouts.calendar", return_value=CAL), \
             mock.patch("harness.scouts.jira", return_value=JIRA), \
             mock.patch("harness.scouts.okr", return_value={"rows": [], "errors": []}), \
             mock.patch("harness.github.snapshot", return_value=GH), \
             mock.patch("harness.claude.run", return_value=synth):
            t = brief.run(self.cfg, self.conn, DAY, NOW)
        self.assertEqual([b["task"] for b in t["blocks"]], ["Fix CI"])  # the block over the meeting is dropped
        self.assertEqual([e["title"] for e in t["tomorrow"]], ["Planning"])
        self.assertEqual(t["meeting_min"], 75)
        text = vault.read_section(vault.note_path(self.cfg, "daily", DAY).read_text(), "brief")
        self.assertIn("Two meetings, one failing PR", text)
        self.assertIn("1. Fix CI on a/b#4: Blocks the release · ~45m · [link](https://gh/4)", text)
        self.assertIn("✗ CI", text)
        self.assertEqual(today.load(self.cfg, DAY)["headline"], SYNTH["headline"])

        line = today.statusline(self.cfg, now=datetime(2026, 11, 2, 9, 10))
        self.assertIn("⏭ 09:30 Standup (20m)", line)
        self.assertIn("◌ no plan", line)
        self.assertIn("⇄ 1 review", line)
        self.assertIn("✗ CI 1", line)
        self.assertIn("● Design review till 15:00", today.statusline(self.cfg, now=datetime(2026, 11, 2, 14, 20)))
        self.assertIn("No plan committed", today.session_context(self.cfg, now=datetime(2026, 11, 2, 9, 0)))

    def test_statusline_without_brief(self):
        self.assertIn("no brief yet", today.statusline(self.cfg, now=NOW))


class SheetsTest(unittest.TestCase):
    def test_pending_rows_and_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = db.connect(Path(tmp) / "me.db")
            db.save_worklog(conn, "2026-11-02", planned={"top3": [{"title": "A"}], "focus_min": 90})
            rows = sheets.pending(conn)
            self.assertEqual(rows[0][0], "2026-11-02")
            self.assertEqual(rows[0][1][2:6], ["A", 0, 0, 1.5])
            self.assertEqual(sheets.csv_text([["a", "b,c"]]), 'a,"b,c"\n')
            conn.close()


if __name__ == "__main__":
    unittest.main()
