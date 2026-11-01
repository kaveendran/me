"""The platform layer (host.py) and the systemd and Task Scheduler backends."""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from harness import guard, host, schedule, setup

from .helpers import make_cfg

TASK_NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


class HostTest(unittest.TestCase):
    def tearDown(self):
        host.kind.cache_clear()

    def test_wsl_paths_from_windows_apps(self):
        with mock.patch("harness.host.kind", return_value="wsl"):
            self.assertEqual(host.native_path(r"C:\Users\ada\notes"), "/mnt/c/Users/ada/notes")
            self.assertEqual(host.native_path("/home/ada/notes"), "/home/ada/notes")
        with mock.patch("harness.host.kind", return_value="linux"):
            self.assertEqual(host.native_path(r"C:\Users\ada\notes"), r"C:\Users\ada\notes")

    def test_git_bash_spellings_of_a_windows_path(self):
        with mock.patch("harness.host.WINDOWS", True):
            forms = host.path_forms(r"C:\Users\ada\notes")
        self.assertEqual(forms, {r"C:\Users\ada\notes", "C:/Users/ada/notes", "/c/Users/ada/notes", "/C/Users/ada/notes"})
        with mock.patch("harness.host.WINDOWS", False):
            self.assertEqual(host.path_forms("/Users/ada/notes"), {"/Users/ada/notes"})

    def test_obsidian_config_locations(self):
        with mock.patch("harness.host.kind", return_value="mac"):
            self.assertEqual(host.obsidian_configs()[0].parts[-4:], ("Library", "Application Support", "obsidian", "obsidian.json"))
        with mock.patch("harness.host.kind", return_value="linux"), mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "/xdg"}):
            found = host.obsidian_configs()
            self.assertEqual(found[0], Path("/xdg/obsidian/obsidian.json"))
            self.assertTrue(any("md.obsidian.Obsidian" in str(p) for p in found))  # Flatpak
        with mock.patch("harness.host.kind", return_value="windows"), mock.patch.dict(os.environ, {"APPDATA": "/appdata"}):
            self.assertEqual(host.obsidian_configs(), [Path("/appdata/obsidian/obsidian.json")])

    def test_obsidian_vaults_merge_every_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "Notes").mkdir()
            (tmp / "work").mkdir()
            a, b, broken = tmp / "a.json", tmp / "b.json", tmp / "broken.json"
            a.write_text(json.dumps({"vaults": {"1": {"path": str(tmp / "Notes")}, "2": {"path": str(tmp / "gone")}}}))
            b.write_text(json.dumps({"vaults": {"3": {"path": str(tmp / "work")}, "4": {"path": str(tmp / "Notes")}}}))
            broken.write_text("{")
            with mock.patch("harness.host.obsidian_configs", return_value=[a, tmp / "missing.json", broken, b]):
                self.assertEqual([v["name"] for v in setup.obsidian_vaults()], ["Notes", "work"])

    def test_open_terminal_without_a_display(self):
        with mock.patch("harness.host.kind", return_value="linux"), mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(host.open_terminal(["gh", "auth", "login"]))
        with mock.patch("harness.host.kind", return_value="wsl"):
            self.assertFalse(host.open_terminal(["gh", "auth", "login"]))

    def test_no_relaunch_outside_windows_or_in_utf8_mode(self):
        with mock.patch("harness.host.WINDOWS", False):
            self.assertIsNone(host.relaunch_utf8(["today"]))
        with mock.patch("harness.host.WINDOWS", True), mock.patch.object(sys, "flags", mock.Mock(utf8_mode=1)):
            self.assertIsNone(host.relaunch_utf8(["today"]))

    def test_windowless_writes_output_to_the_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "logs" / "brief.log"
            saved = sys.stdout, sys.stderr
            try:
                with mock.patch("harness.host.WINDOWS", False):  # don't patch Popen for the rest of the run
                    host.windowless(log)
                print("brief written")
                sys.stdout.close()
            finally:
                sys.stdout, sys.stderr = saved
            self.assertEqual(log.read_text(encoding="utf-8"), "brief written\n")

    def test_kill_group_stops_grandchildren_holding_our_pipe(self):
        # A child that starts a long-lived grandchild sharing its stdout, like claude and its MCP servers.
        code = ("import subprocess, sys, time; "
                "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
                "print('ready', flush=True); time.sleep(60)")
        proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                text=True, **host.new_group())
        try:
            self.assertEqual(proc.stdout.readline().strip(), "ready")
            t0 = time.monotonic()
            self.assertTrue(host.kill_group(proc))
            self.assertEqual(proc.stdout.read(), "")  # EOF: nobody holds the pipe any more
            self.assertLess(time.monotonic() - t0, 15)
            proc.wait(timeout=10)
            deadline = time.monotonic() + 5  # the killed grandchild may linger a moment as a zombie
            while host.kill_group(proc) and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertFalse(host.kill_group(proc))
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.stdout.close()


class GuardWindowsTest(unittest.TestCase):
    def test_git_bash_spelling_of_a_public_vault_is_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(Path(tmp))
            public = str(cfg.zones["public"].vault)
            with mock.patch("harness.host.WINDOWS", True), \
                 mock.patch("harness.host.path_forms", side_effect=lambda p: {p, p + "-as-git-bash"} if p == public else {p}):
                reason = guard.check(cfg, {"tool_name": "Bash", "tool_input": {"command": f"cp x {public}-as-git-bash/Diary/x.md"}})
            self.assertIn("Diary", reason)
            self.assertTrue(guard._names_private_dir(r"git add Diary\2026-11-01.md", ("Diary",)))


class SystemdTest(unittest.TestCase):
    def test_units(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(Path(tmp))
            jobs = schedule.jobs(cfg)
            brief = schedule.units(cfg, "brief", jobs["brief"], "/home/ada/.local/bin/me")
            self.assertEqual(set(brief), {"me-harness-brief.service", "me-harness-brief.timer"})
            service, timer = brief["me-harness-brief.service"], brief["me-harness-brief.timer"]
            self.assertIn("Type=oneshot\n", service)
            self.assertIn("ExecStart=/home/ada/.local/bin/me brief --scheduled\n", service)
            self.assertIn(f"StandardOutput=append:{cfg.state_dir / 'logs' / 'brief.log'}\n", service)
            self.assertIn("OnCalendar=Mon,Tue,Wed,Thu,Fri *-*-* 07:30:00\nPersistent=true\n", timer)
            self.assertIn("WantedBy=timers.target", timer)

            triage = schedule.units(cfg, "triage", jobs["triage"], "/me")["me-harness-triage.timer"]
            self.assertIn("OnActiveSec=1min\nOnUnitActiveSec=30min\n", triage)
            review = schedule.units(cfg, "review", jobs["review"], "/me")["me-harness-review.timer"]
            self.assertIn("OnCalendar=Fri *-*-* 17:00:00", review)

            serve = schedule.units(cfg, "serve", jobs["serve"], "/me")
            self.assertEqual(list(serve), ["me-harness-serve.service"])
            self.assertIn("Restart=always", serve["me-harness-serve.service"])
            self.assertIn("WantedBy=default.target", serve["me-harness-serve.service"])
            self.assertNotIn("Type=oneshot", serve["me-harness-serve.service"])

    def test_paths_with_spaces_are_quoted(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(Path(tmp))
            service = schedule.units(cfg, "tick", schedule.jobs(cfg)["tick"], "/home/a b/me")["me-harness-tick.service"]
            self.assertIn('ExecStart="/home/a b/me" tick --scheduled\n', service)

    def test_install_needs_a_user_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(Path(tmp))
            with mock.patch("harness.host.kind", return_value="wsl"), \
                 mock.patch("harness.schedule.shutil.which", return_value="/me"), \
                 mock.patch("harness.schedule._systemd_ready", return_value=False):
                with self.assertRaises(RuntimeError) as e:
                    schedule.install(cfg)
            self.assertIn("systemd=true", str(e.exception))

    def test_install_status_uninstall(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(Path(tmp))
            units = Path(tmp) / "units"
            calls = []

            def systemctl(*args):
                calls.append(args)
                out = "enabled\n" if args[0] == "is-enabled" else ""
                if args[0] == "show":
                    out = "ExecMainStatus=0\nExecMainStartTimestamp=Sun 2026-11-01 07:30:00 +0530\n"
                return subprocess.CompletedProcess(args, 0, out, "")

            with mock.patch("harness.host.kind", return_value="linux"), \
                 mock.patch("harness.schedule.UNIT_DIR", units), \
                 mock.patch("harness.schedule.shutil.which", side_effect=lambda t: "/opt/bin/" + t), \
                 mock.patch("harness.schedule._systemctl", side_effect=systemctl):
                self.assertEqual(len(schedule.install(cfg)), 7)
                self.assertEqual(len(list(units.glob("*.timer"))), 6)
                self.assertIn(("enable", "--now", "me-harness-brief.timer"), calls)
                self.assertIn(("enable", "--now", "me-harness-serve.service"), calls)
                status = schedule.status(cfg)
                self.assertTrue(all("installed" in s and "not installed" not in s for s in status), status)
                self.assertIn("last exit 0", status[0])
                self.assertEqual(len(schedule.uninstall(cfg)), 13)
                self.assertEqual(list(units.iterdir()), [])
                self.assertTrue(all("not installed" in s for s in schedule.status(cfg)))


class TaskSchedulerTest(unittest.TestCase):
    def xml(self, cfg, name):
        job = schedule.jobs(cfg)[name]
        text = schedule.task_xml(cfg, name, job, r"C:\tools\pythonw.exe", ["-X", "utf8", "-m", "harness", *job["args"]],
                                 r"PC\ada")
        return ET.fromstring(text.split("\n", 1)[1])  # ElementTree won't parse a str that declares UTF-16

    def test_calendar_interval_and_keepalive_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(Path(tmp))
            brief = self.xml(cfg, "brief")
            days = brief.find("t:Triggers/t:CalendarTrigger/t:ScheduleByWeek/t:DaysOfWeek", TASK_NS)
            self.assertEqual([d.tag.split("}")[1] for d in days], ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"])
            self.assertTrue(brief.find("t:Triggers/t:CalendarTrigger/t:StartBoundary", TASK_NS).text.endswith("T07:30:00"))
            exec_ = brief.find("t:Actions/t:Exec", TASK_NS)
            self.assertEqual(exec_.find("t:Command", TASK_NS).text, r"C:\tools\pythonw.exe")
            self.assertEqual(exec_.find("t:Arguments", TASK_NS).text, "-X utf8 -m harness brief --scheduled")
            self.assertEqual(exec_.find("t:WorkingDirectory", TASK_NS).text, str(cfg.home))
            self.assertEqual(brief.find("t:Settings/t:MultipleInstancesPolicy", TASK_NS).text, "IgnoreNew")
            self.assertEqual(brief.find("t:Principals/t:Principal/t:LogonType", TASK_NS).text, "InteractiveToken")

            triage = self.xml(cfg, "triage")
            self.assertEqual(triage.find("t:Triggers/t:TimeTrigger/t:Repetition/t:Interval", TASK_NS).text, "PT30M")

            serve = self.xml(cfg, "serve")
            self.assertIsNotNone(serve.find("t:Triggers/t:LogonTrigger", TASK_NS))
            self.assertEqual(serve.find("t:Triggers/t:TimeTrigger/t:Repetition/t:Interval", TASK_NS).text, "PT5M")
            self.assertEqual(serve.find("t:Settings/t:ExecutionTimeLimit", TASK_NS).text, "PT0S")

    def test_install_status_uninstall(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(Path(tmp))
            created, xmls = [], []

            def schtasks(*args):
                if args[0] == "/Create":
                    created.append(args[2])
                    xmls.append(Path(args[4]).read_text(encoding="utf-16"))
                if args[0] == "/Query":
                    return subprocess.CompletedProcess(args, 0, "".join(f'"{t}","N/A","Ready"\n' for t in created), "")
                if args[0] == "/Delete":
                    created.remove(args[2])
                return subprocess.CompletedProcess(args, 0, "", "")

            with mock.patch("harness.host.kind", return_value="windows"), \
                 mock.patch("harness.host.background_python", return_value=Path(r"C:\venv\Scripts\pythonw.exe")), \
                 mock.patch("harness.schedule.shutil.which", return_value=r"C:\bin\me.exe"), \
                 mock.patch("harness.schedule._schtasks", side_effect=schtasks), \
                 mock.patch("harness.schedule._task_results", return_value={"brief": 0, "serve": 0x41301, "tick": 0x41303}):
                self.assertEqual(len(schedule.install(cfg)), 7)
                self.assertIn("\\me-harness\\brief", created)
                self.assertTrue(xmls[0].startswith('<?xml version="1.0" encoding="UTF-16"?>'))
                status = dict(s.split(": ", 1) for s in schedule.status(cfg))
                self.assertIn("last exit 0", status["\\me-harness\\brief"])
                self.assertIn("running now", status["\\me-harness\\serve"])
                self.assertIn("never run", status["\\me-harness\\tick"])
                self.assertEqual(len(schedule.uninstall(cfg)), 7)
                self.assertTrue(all("not installed" in s for s in schedule.status(cfg)))


class BackendTest(unittest.TestCase):
    def test_backend_per_platform(self):
        for kind, name in (("mac", "launchd"), ("linux", "systemd"), ("wsl", "systemd"), ("windows", "Task Scheduler")):
            with mock.patch("harness.host.kind", return_value=kind):
                self.assertEqual(schedule.backend(), name)


if __name__ == "__main__":
    unittest.main()
