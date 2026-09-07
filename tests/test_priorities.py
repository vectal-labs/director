"""Priority settings exercised through ranking and fake app CLI scans."""
import copy
import json
from pathlib import Path
import tempfile
import time
import unittest

from director import memory
from tests import test_cli
from tests.test_memory import NOW, candidate, review


class PriorityTests(unittest.TestCase):
    def test_missing_file_and_empty_config_use_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "priorities.json"
            self.assertEqual(memory.read_priorities(path), {"projects": {}, "threads": {}})
            path.write_text('{}')
            self.assertEqual(memory.read_priorities(path), {"projects": {}, "threads": {}})

    def test_invalid_settings_are_rejected(self):
        invalid = ['{', 'null', '[]', '{"project": {}}', '{"projects": []}',
                   '{"threads": null}', '{"projects": {"": 1}}']
        for value in ('0', '-1', 'true', 'null', '"0.5"', '[]', '{}', 'NaN', 'Infinity', '1e999'):
            invalid.append('{"threads": {"a": ' + value + '}}')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "priorities.json"
            for content in invalid:
                with self.subTest(content=content):
                    path.write_text(content)
                    with self.assertRaisesRegex(ValueError, "priorities"):
                        memory.read_priorities(path)
            path.write_bytes(b'\xff')
            with self.assertRaisesRegex(ValueError, "priorities"):
                memory.read_priorities(path)
            with self.assertRaisesRegex(ValueError, "priorities"):
                memory.read_priorities(Path(folder))

    def test_project_weight_changes_order_and_thread_override_replaces_it(self):
        config = {"projects": {"deepapi": 0.5}}
        rows = [candidate("deep", project="deepapi"), candidate("normal", idle_min=10)]
        memory.rank(rows, [], NOW, 1, config)
        self.assertEqual([r["id"] for r in rows], ["normal", "deep"])
        self.assertEqual(rows[0]["priority_source"], "default")
        self.assertEqual(rows[1]["priority_source"], "project")
        self.assertEqual(rows[1]["priority_key"], "deepapi")
        config["threads"] = {"deep": 1.5}
        memory.rank(rows, [], NOW, 1, config)
        self.assertEqual(rows[0]["id"], "deep")
        self.assertEqual(rows[0]["priority_weight"], 1.5)
        self.assertEqual(rows[0]["priority_source"], "thread")
        self.assertEqual(rows[0]["priority_key"], "deep")

    def test_weights_cannot_override_any_urgency_level(self):
        for fields in ({"pending_interaction": True}, {"recent_error": "failed"},
                       {"ends_with_question": True}):
            with self.subTest(fields=fields):
                rows = [candidate("normal"), candidate("urgent", **fields)]
                selected = memory.rank(rows, [], NOW, 1, {"threads": {"urgent": 0.01, "normal": 100}})
                self.assertEqual(selected["suggested"], "urgent")

    def test_weight_changes_do_not_bypass_cooldown_or_unknown_input(self):
        for fields in ({}, {"input_history_known": False}):
            with self.subTest(fields=fields):
                rows = [candidate("a", **fields), candidate("b")]
                selected = memory.rank(rows, [review()], NOW, 1, {"threads": {"a": 100}})
                self.assertEqual(selected["suggested"], "b")
                self.assertFalse(rows[1]["review_eligible"])

    def test_defaults_and_equal_weights_preserve_old_tie_breakers(self):
        original = [candidate("c", idle_min=2), candidate("b"), candidate("a")]
        for config in ({}, {"threads": dict.fromkeys("abc", 0.5)}):
            for seed in (1, 2, 99):
                rows = copy.deepcopy(original)
                result = memory.rank(rows, [], NOW, seed, config)
                self.assertEqual([r["id"] for r in rows], ["a", "b", "c"])
                self.assertEqual(result["mode"], "priority")
                self.assertEqual(result["chance"], 0)
                self.assertFalse(any(r["spot_check"] for r in rows))

    def test_cmux_override_follows_session_not_surface(self):
        key = "cmux:claude:session"
        config = {"threads": {key: 2, "OLD-SURFACE": 100}}
        rows = [candidate("NEW-SURFACE", review_key=key),
                candidate("OLD-SURFACE", review_key="cmux:claude:new-session")]
        memory.rank(rows, [], NOW, 1, config)
        self.assertEqual([r["priority_weight"] for r in rows], [2, 1])
        self.assertEqual(rows[0]["priority_key"], key)


class PriorityCliTests(unittest.TestCase):
    def setUp(self):
        self.app = test_cli.CliTests()
        self.app.setUp()
        self.addCleanup(self.app.doCleanups)
        self.config_path = self.app.root / "profile" / "priorities.json"
        self.config_path.parent.mkdir()

    def settings(self, config):
        self.config_path.write_text(json.dumps(config))

    def test_bb_scan_reloads_settings_and_saves_weight_provenance(self):
        data = self.app.fixture["bb"]
        data["threads"].append({**data["threads"][0], "id": "b", "projectId": "other"})
        data["events"]["b"] = data["events"]["a"]
        self.app.save_fixture()
        self.assertEqual(self.app.scan()["selection"]["suggested"], "a")
        config = {"projects": {"p": 0.5}, "threads": {}}
        self.settings(config)
        first = self.app.scan()
        self.assertEqual(first["selection"]["suggested"], "b")
        self.assertEqual(first["priority_config"], config)
        self.assertEqual(first["candidates"][1]["priority_weight"], 0.5)
        self.assertEqual(first["candidates"][1]["priority_source"], "project")
        saved = self.app.root / first["scan"]
        saved_bytes = saved.read_bytes()
        self.assertEqual(json.loads(saved_bytes), first)
        self.app.log_review(first)
        state = first["candidates"][1]["state"]
        self.settings({"threads": {"a": 100}})
        second = self.app.scan()
        self.assertEqual(second["selection"]["suggested"], "b")
        row = next(r for r in second["candidates"] if r["id"] == "a")
        self.assertEqual(row["priority_weight"], 100)
        self.assertEqual(row["priority_source"], "thread")
        self.assertEqual(row["state"], state)
        self.assertEqual(row["eligibility_reason"], "leave_cooldown")
        self.assertEqual(saved.read_bytes(), saved_bytes)
        self.assertNotIn("cmux", self.app.called())

    def test_invalid_config_stops_before_app_access_and_writes_no_scan(self):
        self.settings({"projects": {"p": -0.5}})
        result = self.app.cli("scan.py", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("projects.p must be a finite number greater than zero", result.stderr)
        self.assertEqual(self.app.called(), [])
        self.assertFalse((self.app.root / "state" / "scans").exists())

    def test_high_weights_do_not_include_running_or_recently_contacted_threads(self):
        data = self.app.fixture["bb"]
        for name, status in (("running", "active"), ("recent", "idle")):
            data["threads"].append({**data["threads"][0], "id": name, "status": status})
        data["events"]["recent"] = [{"type": "client/turn/requested", "createdAt": time.time() * 1000,
                                      "data": {"initiator": "user"}}]
        self.app.save_fixture()
        self.settings({"threads": {"running": 100, "recent": 100}})
        scan = self.app.scan()
        self.assertEqual([r["id"] for r in scan["candidates"]], ["a"])
        self.assertEqual(scan["skipped_user_recent"], ["recent"])

    def test_cmux_scan_supports_project_path_and_session_override(self):
        self.app.cmux_fixture()
        first = self.app.scan(env=self.app.cmux_env())
        row = first["candidates"][0]
        self.settings({"projects": {row["project"]: 0.5}})
        weighted = self.app.scan(env=self.app.cmux_env())["candidates"][0]
        self.assertEqual(weighted["priority_weight"], 0.5)
        self.settings({"projects": {row["project"]: 0.5}, "threads": {row["review_key"]: 2}})
        overridden = self.app.scan(env=self.app.cmux_env())["candidates"][0]
        self.assertEqual(overridden["priority_weight"], 2)
        self.assertEqual(overridden["priority_source"], "thread")
        self.assertNotIn("bb", self.app.called())


if __name__ == "__main__":
    unittest.main()
