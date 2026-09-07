"""Profile isolation, recovery, and configurable behavior through fake app CLIs."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from director import preferences
from tests import test_lessons


class SettingsTests(unittest.TestCase):
    def test_defaults_are_neutral_and_independent(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "missing.json"
            first = preferences.read(path)
            self.assertEqual(first["spot_check_chance"], 0)
            self.assertEqual(first["legacy_override_decisions"], {})
            first["legacy_override_decisions"]["example"] = "leave"
            self.assertEqual(preferences.read(path)["legacy_override_decisions"], {})
            self.assertFalse(path.exists())

    def test_invalid_settings_are_rejected(self):
        invalid = [None, [], {"unknown": True}, {"legacy_override_decisions": []},
                   {"legacy_override_decisions": {"Q1": "guess"}},
                   {"legacy_override_decisions": {" ": "leave"}}, {"spot_check_chance": 1.1}]
        for field in ("recheck_seconds", "recent_user_seconds", "spot_check_chance"):
            invalid.extend({field: value} for value in (-1, True, None, "1", [], 10**400, float("nan"), float("inf")))
        for config in invalid:
            with self.subTest(config=config), self.assertRaises(ValueError):
                preferences.validate(config)


class ProfileRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.app = self.fixture_app()

    def fixture_app(self):
        app = test_lessons.LessonTests()
        app.setUp()
        self.addCleanup(app.doCleanups)
        return app

    def settings(self, config):
        path = self.app.root / "profile" / "settings.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(config))

    def journal(self, folder, app=None):
        return (app or self.app).root / folder / "lessons.jsonl"

    def events(self, path):
        return [json.loads(line) for line in path.read_text().splitlines()]

    def teach(self, kind="general_preference", **scope):
        self.app.log_review(self.app.scan())
        self.app.correction(self.app.lesson(kind, **scope))

    def test_durable_and_temporary_lessons_have_separate_journals_with_exact_sources(self):
        self.teach(scope="general")
        self.app.correction(self.app.lesson("temporary_instruction", ends_when="Operator releases this hold."))
        self.app.correction(self.app.lesson("exception"))
        history = self.app.events()
        self.assertEqual(self.events(self.journal("profile")), [history[1]])
        self.assertEqual(self.events(self.journal("state")), history[2:])
        self.app.cli("log.py", "end-lesson", "--id", "1.2", "--evidence", "Operator released it.")
        self.assertEqual(self.events(self.journal("state"))[-1], self.app.events()[-1])
        self.assertEqual([r["id"] for r in self.app.rows()["a"]["lessons"]], ["1.1", "1.3"])

    def test_copying_only_profile_preserves_scope_sources_expiry_and_endings(self):
        self.teach(scope="general")
        self.app.correction(self.app.lesson("project_decision", scope="project"), david="Exact project decision.")
        self.app.correction(self.app.lesson(expires_at="2020-01-01T00:00:00Z"))
        self.app.correction(self.app.lesson(), david="Withdraw this preference later.")
        self.app.cli("log.py", "end-lesson", "--id", "1.4", "--evidence", "Operator withdrew it.")
        self.app.correction(self.app.lesson("temporary_instruction", ends_when="The experiment ends."))
        fresh = self.fixture_app()
        shutil.copytree(self.app.root / "profile", fresh.root / "profile")
        fresh.add_bb_thread("b", "other-project")
        rows = fresh.rows()
        self.assertEqual([r["id"] for r in rows["a"]["lessons"]], ["1.1", "1.2"])
        self.assertEqual([r["id"] for r in rows["b"]["lessons"]], ["1.1"])
        self.assertEqual(rows["a"]["lessons"][1]["david"], "Exact project decision.")
        self.assertEqual(rows["a"]["lessons"][1]["rule"], "test-correction")
        self.assertEqual(rows["a"]["history"]["pick_count"], 0)
        self.assertFalse(fresh.log_path.exists())
        self.assertFalse(self.journal("state", fresh).exists())
        self.assertEqual(self.journal("profile", fresh).read_bytes(), self.journal("profile").read_bytes())

    def test_imported_lesson_can_end_without_original_review_and_new_ids_do_not_collide(self):
        self.teach(scope="general")
        fresh = self.fixture_app()
        shutil.copytree(self.app.root / "profile", fresh.root / "profile")
        fresh.log_review(fresh.scan())
        fresh.correction(fresh.lesson(scope="general"), david="New installation teaching.")
        self.assertEqual([r["id"] for r in fresh.rows()["a"]["lessons"]], ["1.1", "1.2"])
        before = fresh.log_path.read_bytes()
        fresh.cli("log.py", "end-lesson", "--id", "1.1", "--evidence", "Operator withdrew the imported preference.")
        self.assertEqual(fresh.log_path.read_bytes(), before)
        self.assertEqual([r["id"] for r in fresh.rows()["a"]["lessons"]], ["1.2"])
        self.assertEqual(self.events(self.journal("profile", fresh))[-1]["lesson_id"], "1.1")
        self.assertEqual(fresh.rows()["a"]["history"]["pick_count"], 1)

    def test_profile_lesson_can_end_with_no_log_at_all(self):
        self.teach(scope="general")
        fresh = self.fixture_app()
        shutil.copytree(self.app.root / "profile", fresh.root / "profile")
        fresh.cli("log.py", "end-lesson", "--id", "1.1", "--evidence", "Operator withdrew it.")
        self.assertEqual(fresh.rows()["a"]["lessons"], [])
        self.assertFalse(fresh.log_path.exists())

    def test_interrupted_journal_projection_repairs_all_events_without_rewriting_history(self):
        self.teach(scope="general")
        self.app.correction(self.app.lesson("temporary_instruction", ends_when="The experiment ends."))
        self.app.cli("log.py", "end-lesson", "--id", "1.1", "--evidence", "Operator withdrew it.")
        self.app.cli("log.py", "end-lesson", "--id", "1.2", "--evidence", "The experiment ended.")
        profile = self.journal("profile").read_bytes()
        state = self.journal("state").read_bytes()
        history = self.app.log_path.read_bytes()
        self.journal("profile").unlink()
        self.journal("state").unlink()
        self.assertEqual(self.app.rows()["a"]["lessons"], [])
        self.assertEqual(self.journal("profile").read_bytes(), profile)
        self.assertEqual(self.journal("state").read_bytes(), state)
        self.assertEqual(self.app.log_path.read_bytes(), history)
        self.app.scan()
        self.assertEqual(self.journal("profile").read_bytes(), profile)

    def test_journal_only_end_is_not_resurrected_by_older_history(self):
        self.teach(scope="general")
        historical = self.app.log_path.read_bytes()
        self.app.cli("log.py", "end-lesson", "--id", "1.1", "--evidence", "Operator withdrew it.")
        self.app.log_path.write_bytes(historical)
        profile = self.journal("profile").read_bytes()
        self.assertEqual(self.app.rows()["a"]["lessons"], [])
        self.assertEqual(self.journal("profile").read_bytes(), profile)
        self.assertEqual(self.app.log_path.read_bytes(), historical)

    def test_conflicting_sources_stop_before_app_access_and_do_not_change_files(self):
        self.teach(scope="general")
        events = self.events(self.journal("profile"))
        events[0]["david"] = "A different source with the same ID."
        self.journal("profile").write_text(json.dumps(events[0]) + "\n")
        profile = self.journal("profile").read_bytes()
        history = self.app.log_path.read_bytes()
        self.app.calls.unlink()
        result = self.app.cli("scan.py", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("conflicting source for lesson 1.1", result.stderr)
        self.assertEqual(self.app.called(), [])
        result = self.app.correction(self.app.lesson(), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.journal("profile").read_bytes(), profile)
        self.assertEqual(self.app.log_path.read_bytes(), history)

    def test_temporary_lessons_cannot_be_silently_promoted_by_copying_journal(self):
        self.teach("temporary_instruction", ends_when="Operator releases this hold.")
        fresh = self.fixture_app()
        self.journal("profile", fresh).parent.mkdir()
        shutil.copyfile(self.journal("state"), self.journal("profile", fresh))
        result = fresh.cli("scan.py", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("wrong journal", result.stderr)
        self.assertEqual(fresh.called(), [])

    def test_settings_control_cooldown_and_bb_recent_user_window(self):
        first = self.app.scan()
        self.app.log_review(first)
        self.assertEqual(self.app.rows()["a"]["eligibility_reason"], "leave_cooldown")
        self.settings({"recheck_seconds": 0, "recent_user_seconds": 0})
        second = self.app.scan()
        self.assertEqual(second["candidates"][0]["eligibility_reason"], "leave_expired")
        self.assertEqual(second["settings"]["recheck_seconds"], 0)
        self.app.fixture["bb"]["events"]["a"].append(
            {"type": "client/turn/requested", "createdAt": int((time.time() - 30) * 1000),
             "data": {"initiator": "user"}})
        self.app.save_fixture()
        self.assertEqual([r["id"] for r in self.app.scan()["candidates"]], ["a"])
        self.settings({"recent_user_seconds": 60})
        self.assertEqual(self.app.scan()["skipped_user_recent"], ["a"])
        self.assertNotIn("cmux", self.app.called())

    def test_cmux_uses_configured_recent_user_window(self):
        self.app.cmux_fixture()
        self.assertEqual(self.app.scan(env=self.app.cmux_env())["skipped_user_recent"], ["FRESH"])
        self.settings({"recent_user_seconds": 0})
        result = self.app.scan(env=self.app.cmux_env())
        self.assertEqual(result["skipped_user_recent"], [])
        self.assertEqual({r["id"] for r in result["candidates"]}, {"FRESH", "STOPPED"})
        self.assertNotIn("bb", self.app.called())

    def test_bad_settings_stop_scan_before_app_access(self):
        self.settings({"spot_check_chance": -1})
        result = self.app.cli("scan.py", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("settings spot_check_chance", result.stderr)
        self.assertEqual(self.app.called(), [])
        self.assertFalse((self.app.root / "state" / "scans").exists())

    def test_module_invocation_and_historical_scan_paths_work_outside_repo(self):
        result = subprocess.run([sys.executable, "-m", "director.scan", "--seed", "1"],
                                cwd=self.app.root, env=self.app.env, capture_output=True, text=True, check=True)
        scan = json.loads(result.stdout)
        self.assertTrue(scan["scan"].startswith("state/scans/"))
        for path in (scan["scan"], scan["scan"].removeprefix("state/"),
                     scan["scan"].replace("state/", "private/", 1)):
            result = subprocess.run([sys.executable, str(self.app.root / "director" / "log.py"), "run",
                                     "--picked", "a", "--decision", "leave", "--seen", "finished",
                                     "--reason", "complete", "--scan", path],
                                    cwd=self.app.root.parent, env=self.app.env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.app.events()), 3)


if __name__ == "__main__":
    unittest.main()
