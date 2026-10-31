import tempfile
import unittest
from pathlib import Path

from harness.config import ConfigError, autonomy_level, load_autonomy, parse

from .helpers import REPO, example_raw


class ConfigTest(unittest.TestCase):
    def test_example_parses_without_public_vault(self):
        cfg = parse(example_raw(), REPO)
        self.assertNotIn("public", cfg.zones)
        self.assertNotIn("knowledge", cfg.routing)
        self.assertFalse(cfg.zone_for("diary").public)
        self.assertIn("Diary", cfg.private_dirs)

    def test_public_vault_when_set(self):
        raw = example_raw()
        raw["zones"]["public"]["vault"] = "~/notes"
        cfg = parse(raw, REPO)
        self.assertTrue(cfg.zones["public"].public)
        self.assertEqual(cfg.routing["knowledge"], "public")

    def test_private_kind_cannot_route_to_public_zone(self):
        raw = example_raw()
        raw["zones"]["public"]["vault"] = "~/notes"
        raw["routing"]["diary"] = "public"
        with self.assertRaisesRegex(ConfigError, "diary must route to a private zone"):
            parse(raw, REPO)

    def test_unknown_zone(self):
        raw = example_raw()
        raw["routing"]["daily"] = "nowhere"
        with self.assertRaisesRegex(ConfigError, "unknown zone"):
            parse(raw, REPO)

    def test_routed_kind_needs_path_template(self):
        raw = example_raw()
        del raw["paths"]["inbox"]
        with self.assertRaisesRegex(ConfigError, "missing template"):
            parse(raw, REPO)

    def test_schedule_validation(self):
        for key, bad in (("brief", "7:30"), ("quiet_hours", "21:30"), ("weekly", "Friday 17:00")):
            raw = example_raw()
            raw["schedule"][key] = bad
            with self.subTest(key=key), self.assertRaises(ConfigError):
                parse(raw, REPO)


class AutonomyTest(unittest.TestCase):
    def test_repo_policy_loads_and_falls_back_to_default(self):
        policy = load_autonomy(home=REPO)
        self.assertEqual(autonomy_level(policy, "github", "merge"), "off")
        self.assertEqual(autonomy_level(policy, "calendar", "break_buffer"), "auto")
        self.assertEqual(autonomy_level(policy, "github", "something_new"), "approve")

    def test_invalid_level_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "autonomy.toml"
            path.write_text('[jira]\ntransition = "yolo"\n')
            with self.assertRaisesRegex(ConfigError, "auto|approve|off"):
                load_autonomy(path)


if __name__ == "__main__":
    unittest.main()
