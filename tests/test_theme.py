"""Theme engine: colour maths, contrast, the adaptive state, prefs and the API."""

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import closing
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from harness import db, server, setup, theme
from harness.config import ConfigError, set_value

from .test_setup import SetupBase

LOAD_OK = {"score": 10, "band": "ok", "reasons": []}


class ColourTest(unittest.TestCase):
    def test_oklch_round_trip_and_gamut(self):
        for hex_ in ("#4451c9", "#0d8a7c", "#ffffff", "#000000", "#f5ae39"):
            self.assertEqual(theme.oklch(*theme.to_oklch(hex_)), hex_)
        self.assertRegex(theme.oklch(0.7, 0.4, 140), r"^#[0-9a-f]{6}$")  # far out of gamut: chroma is reduced

    def test_every_palette_is_readable(self):
        for t in theme.THEMES.values():
            for state in [None, *theme.STATES.values()]:
                for mode in ("dark", "light"):
                    p = theme.palette(t, mode, state)
                    for fg in ("text", "text-2", "muted", "dim", "accent", "accent-2", "ok", "warn", "bad"):
                        for bg in ("bg", "surface", "surface-2"):
                            with self.subTest(theme=t.id, state=state and state.id, mode=mode, fg=fg, bg=bg):
                                self.assertGreaterEqual(theme.contrast(p[fg], p[bg]), 4.5)
                    self.assertGreaterEqual(theme.contrast(p["accent-ink"], p["accent"]), 4.5)

    def test_states_change_the_look_and_calm_drops_glow(self):
        t = theme.THEMES["aurora"]
        steady, busy, strained = (theme.palette(t, "dark", theme.STATES[s]) for s in ("steady", "busy", "strained"))
        self.assertEqual(steady["accent"], theme.palette(t, "dark")["accent"])
        self.assertNotEqual(busy["accent"], steady["accent"])
        self.assertNotEqual(strained["bg"], steady["bg"])  # the greys take the state's tint
        self.assertLess(theme.to_oklch(strained["accent"])[1], theme.to_oklch(steady["accent"])[1])  # calmer = less saturated
        self.assertIn("0.048", strained["glow-1"])

    def test_custom_accent_keeps_its_hue(self):
        p = theme.palette(theme.THEMES["graphite"], "light", accent="#e0457b")
        self.assertLess(abs(theme.to_oklch(p["accent"])[2] - theme.to_oklch("#e0457b")[2]), 3)

    def test_terminal_mapping(self):
        self.assertEqual(theme.xterm256("#000000"), 16)
        self.assertEqual(theme.xterm256("#ff0000"), 196)
        self.assertEqual(theme.xterm256("#808080"), 244)
        self.assertEqual(theme.basic8("#808080"), -1)
        self.assertEqual(theme.basic8("#4451c9"), 4)
        self.assertEqual(theme.basic8("#0d8a7c"), 6)
        with mock.patch.dict("os.environ", {"COLORFGBG": "0;15"}):
            self.assertEqual(theme.terminal_mode("system"), "light")
        self.assertEqual(theme.terminal_mode("dark"), "dark")


class ChooseTest(unittest.TestCase):
    def test_priorities(self):
        choose = theme.choose
        self.assertEqual(choose(LOAD_OK, {}, 0, None), ("steady", []))
        self.assertEqual(choose(LOAD_OK, {"mood": 4}, 0, None)[0], "flow")
        self.assertEqual(choose({"score": 40, "band": "elevated", "reasons": ["5.0h of meetings"]}, {"mood": 5}, 0, None),
                         ("busy", ["5.0h of meetings"]))
        self.assertEqual(choose(LOAD_OK, {}, 130, None), ("busy", ["130 min without a break"]))
        self.assertEqual(choose(LOAD_OK, {"mood": 5}, 0, "Sunday, not a workday")[0], "rest")
        state, reasons = choose(LOAD_OK, {"mood": 2}, 0, "Sunday, not a workday")  # how you feel outranks the clock
        self.assertEqual((state, reasons), ("strained", ["latest check-in: mood 2/5"]))
        self.assertEqual(choose(LOAD_OK, {"stress": 4}, 0, None)[0], "strained")
        self.assertEqual(choose({"score": 70, "band": "high", "reasons": []}, {"mood": 5}, 0, None)[0], "strained")


class PrefsTest(SetupBase):
    def test_save_validates_and_creates_the_section(self):
        cfg = setup.ensure_config(self.home)
        self.assertEqual(theme.prefs(cfg), {"name": "aurora", "mode": "system", "adaptive": True, "accent": ""})
        for bad in ({"name": "neon"}, {"mode": "dim"}, {"adaptive": "yes"}, {"accent": "red"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                theme.save_prefs(cfg, bad)
        (self.home / "me.toml").write_text((self.home / "me.toml").read_text().split("\n# Colour theme")[0])  # an older me.toml
        cfg = theme.save_prefs(cfg, {"name": "ember", "adaptive": False, "accent": "#E0457B"})
        self.assertEqual(theme.prefs(cfg), {"name": "ember", "mode": "system", "adaptive": False, "accent": "#e0457b"})
        self.assertIn('[theme]\nname = "ember"\nadaptive = false\naccent = "#e0457b"\n', (self.home / "me.toml").read_text())

    def test_snapshot_and_api(self):
        cfg = setup.ensure_config(self.home)
        now = datetime(2026, 11, 2, 10, 0)  # a Monday, inside work hours
        with closing(db.connect(cfg.db_path)) as conn, \
                mock.patch("harness.wellbeing.current_streak_min", return_value=0), \
                mock.patch("harness.activity.timeline", return_value=[]):
            conn.execute("INSERT INTO checkins (ts, mood, source) VALUES (?, ?, ?)", ("2026-11-02T09:30:00+05:30", 2, "test"))
            conn.commit()
            snap = theme.snapshot(cfg, conn, now=now)
            self.assertEqual((snap["state"]["id"], snap["applied"]["id"], snap["applied"]["calm"]), ("strained", "strained", True))
            self.assertEqual(snap["palette"]["dark"], theme.palette(theme.THEMES["aurora"], "dark", theme.STATES["strained"]))
            self.assertEqual(theme.snapshot(cfg, conn, preview="flow", now=now)["applied"], {"id": "flow", "calm": False, "preview": True})
            self.assertEqual(len(snap["themes"]), len(theme.THEMES))
            cfg = theme.save_prefs(cfg, {"adaptive": False})
            off = theme.snapshot(cfg, conn, now=now)
            self.assertEqual((off["state"]["id"], off["applied"]["id"]), ("strained", "steady"))  # still reported, not applied

        secret = server.token(cfg)
        (self.home / "ui" / "dist").mkdir(parents=True)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), None)
        port = httpd.server_address[1]
        httpd.RequestHandlerClass = server.make_handler(cfg, secret, {f"127.0.0.1:{port}"}, self.home / "ui" / "dist")
        threading.Thread(target=httpd.serve_forever, daemon=True).start()

        def call(path, body=None):
            req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode() if body is not None else None,
                                         method="POST" if body is not None else "GET", headers={"X-Me-Token": secret})
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                with e:
                    return e.code, json.loads(e.read())
        try:
            with mock.patch("harness.wellbeing.current_streak_min", return_value=0):
                status, snap = call("/api/theme?preview=busy")
                self.assertEqual((status, snap["applied"]["id"]), (200, "busy"))
                self.assertEqual(call("/api/theme?preview=nope")[0], 400)
                status, snap = call("/api/theme", {"name": "fjord", "mode": "light"})
                self.assertEqual((status, snap["prefs"]["name"], snap["prefs"]["mode"]), (200, "fjord", "light"))
                self.assertEqual(call("/api/theme", {"mode": "sepia"})[0], 400)
        finally:
            httpd.shutdown()
            httpd.server_close()


class SetValueTest(unittest.TestCase):
    def test_create_and_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "me.toml"
            path.write_text('[a]\nx = "1"\n\n[b]\ny = "2"')
            set_value(path, "a", "flag", True, create=True)
            set_value(path, "c", "name", 'say "hi"', create=True)
            set_value(path, "a", "x", False)
            self.assertEqual(path.read_text(), '[a]\nx = false\nflag = true\n\n[b]\ny = "2"\n\n[c]\nname = "say \\"hi\\""\n')
            with self.assertRaises(ConfigError):
                set_value(path, "b", "missing", "v")


if __name__ == "__main__":
    unittest.main()
