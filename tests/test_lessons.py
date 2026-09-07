"""Correction boundaries exercised through the real CLI and fake app fixtures."""
import datetime as dt
import json
import subprocess
import sys
import unittest

from tests import test_cli


class LessonTests(unittest.TestCase):
    setUp = test_cli.CliTests.setUp
    save_fixture = test_cli.CliTests.save_fixture
    called = test_cli.CliTests.called
    cli = test_cli.CliTests.cli
    scan = test_cli.CliTests.scan
    log_review = test_cli.CliTests.log_review
    log_path = test_cli.CliTests.log_path
    cmux_env = test_cli.CliTests.cmux_env
    cmux_fixture = test_cli.CliTests.cmux_fixture

    def lesson(self, kind="general_preference", **changes):
        return {"kind": kind, "interpretation": "Keep this investigation with its current owner.",
                "reason": "Changing owners would repeat the investigation.",
                "applies_when": "The current owner can finish the investigation.", **changes}

    def correction(self, lesson=None, david="Keep this one with its current owner.", run=1,
                   decision="leave", **kwargs):
        args = ["override", "--run", str(run), "--david", david, "--decision", decision,
                "--rule", "test-correction"]
        if lesson is not None:
            args.extend(["--lesson", json.dumps(lesson, ensure_ascii=False)])
        return self.cli("log.py", *args, **kwargs)

    def events(self):
        return [json.loads(line) for line in self.log_path.read_text().splitlines()]

    def folded(self):
        result = subprocess.run(
            [sys.executable, "-c", "import json, memory; print(json.dumps(memory.read()))"],
            env=self.env, cwd=self.root / "director", capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    def rows(self, **kwargs):
        return {row["id"]: row for row in self.scan(**kwargs)["candidates"]}

    def add_bb_thread(self, thread_id, project="p"):
        data = self.fixture["bb"]
        data["threads"].append({**data["threads"][0], "id": thread_id, "projectId": project})
        data["events"][thread_id] = list(data["events"]["a"])
        self.save_fixture()

    def test_unspecified_scope_stays_in_original_thread(self):
        self.add_bb_thread("b")
        self.log_review(self.scan())
        self.correction(self.lesson())
        rows = self.rows()
        lesson = rows["a"]["lessons"][0]
        self.assertEqual((lesson["id"], lesson["scope"], lesson["target"], lesson["app"]),
                         ("1.1", "thread", "a", "bb"))
        self.assertEqual(rows["b"]["lessons"], [])
        self.assertEqual(rows["a"]["eligibility_reason"], "leave_cooldown")
        self.assertEqual(rows["b"]["eligibility_reason"], "never_reviewed")
        self.assertEqual(self.folded()[0]["action_status"], "none")
        self.assertNotIn("cmux", self.called())

    def test_lesson_requires_original_words(self):
        self.log_review(self.scan())
        before = self.log_path.read_bytes()
        result = self.correction(self.lesson(), david=" \n", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exact, nonblank words", result.stderr)
        self.assertEqual(self.log_path.read_bytes(), before)

    def test_incomplete_stored_scope_stops_scan_without_guessing(self):
        self.log_review(self.scan())
        self.correction(self.lesson())
        events = self.events()
        del events[-1]["lesson"]["scope"]
        self.log_path.write_text("\n".join(json.dumps(event) for event in events) + "\n")
        before = self.log_path.read_bytes()
        result = self.cli("scan.py", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid record", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(self.log_path.read_bytes(), before)

    def test_project_decision_applies_only_to_same_project_and_app(self):
        self.fixture["bb"]["threads"][0]["projectId"] = "/repo"
        self.add_bb_thread("b", "/repo")
        self.add_bb_thread("c", "/repo-other")
        self.log_review(self.scan())
        self.correction(self.lesson("project_decision", scope="project"))
        rows = self.rows()
        self.assertEqual([lesson["id"] for lesson in rows["a"]["lessons"]], ["1.1"])
        self.assertEqual([lesson["id"] for lesson in rows["b"]["lessons"]], ["1.1"])
        self.assertEqual(rows["a"]["lessons"][0]["target"], "/repo")
        self.assertEqual(rows["c"]["lessons"], [])
        self.cmux_fixture()  # Same project spelling in another app does not establish identity.
        self.assertEqual(self.rows(env=self.cmux_env())["STOPPED"]["lessons"], [])

    def test_explicit_general_preference_can_apply_across_projects_and_apps(self):
        self.add_bb_thread("b", "other-project")
        self.log_review(self.scan())
        self.correction(self.lesson(scope="general"))
        rows = self.rows()
        self.assertEqual([lesson["id"] for lesson in rows["b"]["lessons"]], ["1.1"])
        self.assertIsNone(rows["b"]["lessons"][0]["target"])
        self.cmux_fixture()
        other_app = self.rows(env=self.cmux_env())["STOPPED"]["lessons"]
        self.assertEqual([lesson["id"] for lesson in other_app], ["1.1"])

    def test_exception_does_not_spread_and_exact_words_stay_separate(self):
        self.add_bb_thread("b")
        self.log_review(self.scan())
        before = self.log_path.read_bytes()
        words = "  This is an exception.\nDon't make it a rule — only this handoff.  "
        supplied = self.lesson("exception", interpretation="Leave this particular handoff with its owner.")
        self.correction(supplied, david=words)
        self.assertTrue(self.log_path.read_bytes().startswith(before))
        event = self.events()[-1]
        self.assertEqual(event["david"], words)
        for key, value in supplied.items():
            self.assertEqual(event["lesson"][key], value)
        rows = self.rows()
        self.assertEqual(rows["b"]["lessons"], [])
        exposed = rows["a"]["lessons"][0]
        self.assertEqual(exposed["david"], words)
        self.assertEqual(exposed["interpretation"], supplied["interpretation"])
        self.assertEqual((exposed["run"], exposed["rule"]), (1, "test-correction"))

    def test_conditions_remain_visible_until_explicit_end_evidence(self):
        self.log_review(self.scan())
        instruction = self.lesson("temporary_instruction", ends_when="Tests pass.",
                                  applies_when="David is investigating this thread.")
        self.correction(instruction)
        # Similar text in an agent response cannot silently end an operator instruction.
        self.fixture["bb"]["events"]["a"][0]["data"]["item"]["text"] = "Tests pass."
        self.save_fixture()
        active = self.rows()["a"]["lessons"]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["ends_when"], instruction["ends_when"])
        self.assertEqual(active[0]["applies_when"], instruction["applies_when"])
        before = self.log_path.read_bytes()
        self.cli("log.py", "end-lesson", "--id", "1.1", "--evidence", "David released the investigation hold.")
        self.assertTrue(self.log_path.read_bytes().startswith(before))
        self.assertEqual(self.rows()["a"]["lessons"], [])
        self.assertEqual(self.events()[-1]["kind"], "lesson_end")

    def test_expiry_filters_active_lessons_without_erasing_history(self):
        self.log_review(self.scan())
        now = dt.datetime.now(dt.timezone.utc)
        past = (now - dt.timedelta(days=1)).isoformat()
        future = (now + dt.timedelta(days=1)).isoformat()
        self.correction(self.lesson("temporary_instruction", expires_at=past))
        self.correction(self.lesson("temporary_instruction", expires_at=future))
        before = self.log_path.read_bytes()
        active = self.rows()["a"]["lessons"]
        self.assertEqual([lesson["id"] for lesson in active], ["1.2"])
        self.assertEqual(active[0]["expires_at"], future)
        self.assertEqual(len(self.folded()[0]["corrections"]), 2)
        self.assertEqual(self.log_path.read_bytes(), before)

    def test_multiple_corrections_are_independent_and_end_keeps_review_time(self):
        self.log_review(self.scan())
        original = self.events()[0]
        self.correction(self.lesson("temporary_instruction", ends_when="David releases the hold."))
        self.correction(self.lesson(scope="project", interpretation="Keep updates concise."),
                        david="Keep updates concise.")
        self.assertEqual([lesson["id"] for lesson in self.rows()["a"]["lessons"]], ["1.1", "1.2"])
        before = self.log_path.read_bytes()
        self.cli("log.py", "end-lesson", "--id", "1.1", "--evidence", "David released the hold.")
        self.assertTrue(self.log_path.read_bytes().startswith(before))
        row = self.rows()["a"]
        self.assertEqual([lesson["id"] for lesson in row["lessons"]], ["1.2"])
        self.assertEqual(row["history"]["last_review_at"], original["reviewed_at"])
        self.assertEqual(row["history"]["pick_count"], 1)
        self.assertEqual(row["history"]["last_decision"], "leave")
        self.assertEqual(row["eligibility_reason"], "leave_cooldown")
        folded = self.folded()
        self.assertEqual(len(folded), 1)
        self.assertEqual(len(folded[0]["corrections"]), 2)
        self.assertEqual(folded[0]["david_override"]["david"], "Keep updates concise.")
        self.assertIn("runs 1", self.cli("log.py", "stats").stdout)

    def test_legacy_corrections_stay_history_without_becoming_active_lessons(self):
        self.log_review(self.scan())
        self.correction(david="Continue this work.", decision="unblock")
        first = self.rows()["a"]
        self.assertEqual(first["lessons"], [])
        self.assertEqual(first["history"]["last_decision"], "unblock")
        self.assertEqual(first["eligibility_reason"], "needs_review")
        self.correction(self.lesson(), david="Only this investigation.")
        second = self.rows()["a"]
        self.assertEqual([lesson["id"] for lesson in second["lessons"]], ["1.2"])
        self.correction(david="The latest correction has no reusable scope.")
        final = self.rows()["a"]
        self.assertEqual([lesson["id"] for lesson in final["lessons"]], ["1.2"])
        self.assertEqual(len(self.folded()[0]["corrections"]), 3)
        self.assertEqual(final["history"]["last_override"]["david"],
                         "The latest correction has no reusable scope.")

    def test_invalid_lesson_inputs_do_not_append(self):
        self.log_review(self.scan())
        before = self.log_path.read_bytes()
        valid = self.lesson()
        invalid = [[], "not an object", {**valid, "kind": "rule"}, {**valid, "scope": "everywhere"},
                   self.lesson("project_decision", scope="thread"),
                   self.lesson("project_decision", scope="general"),
                   self.lesson("exception", scope="project"), self.lesson("exception", scope="general"),
                   self.lesson("temporary_instruction"), self.lesson("temporary_instruction", ends_when="  "),
                   self.lesson("temporary_instruction", expires_at="2027-01-01T00:00:00"),
                   self.lesson("temporary_instruction", expires_at="not-a-date"),
                   self.lesson("temporary_instruction", expires_at="2027-02-30T00:00:00Z"),
                   {**valid, "app": "cmux"}, {**valid, "target": "another-thread"},
                   {**valid, "id": "forged"}, {**valid, "david": "invented words"}]
        for field in ("interpretation", "reason", "applies_when"):
            missing = dict(valid)
            missing.pop(field)
            invalid.extend([missing, {**valid, field: " \n"}, {**valid, field: False}])
        for index, supplied in enumerate(invalid):
            with self.subTest(case=index, supplied=supplied):
                result = self.correction(supplied, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.log_path.read_bytes(), before)
        malformed = self.cli("log.py", "override", "--run", "1", "--david", "Real correction.",
                             "--decision", "leave", "--lesson", "{", check=False)
        self.assertNotEqual(malformed.returncode, 0)
        self.assertEqual(self.log_path.read_bytes(), before)
        self.correction(valid)
        self.assertEqual(self.rows()["a"]["lessons"][0]["id"], "1.1")

    def test_scoped_lesson_needs_recorded_context(self):
        self.log_review()  # Legacy review has no saved scan or trustworthy app binding.
        before = self.log_path.read_bytes()
        result = self.correction(self.lesson(), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.log_path.read_bytes(), before)
        self.correction(david="A legacy correction can still be recorded.")
        self.assertEqual(self.rows()["a"]["lessons"], [])

    def test_inline_legacy_correction_is_retained_without_inferred_scope(self):
        self.log_review(self.scan())
        profile = self.root / "profile"
        profile.mkdir(exist_ok=True)
        (profile / "settings.json").write_text(json.dumps({"legacy_override_decisions": {"Q20": "leave"}}))
        legacy = self.events()[0]
        legacy["david_override"] = {"david": "This older instruction has no documented scope.", "rule": "Q20"}
        self.log_path.write_text(json.dumps(legacy))
        before = self.log_path.read_bytes()
        row = self.rows()["a"]
        self.assertEqual(row["lessons"], [])
        self.assertEqual(row["history"]["last_decision"], "leave")
        self.correction(self.lesson("exception"))
        self.assertTrue(self.log_path.read_bytes().startswith(before))
        corrections = self.folded()[0]["corrections"]
        self.assertEqual(corrections[0], legacy["david_override"])
        self.assertEqual(corrections[1]["lesson"]["id"], "1.2")
        self.assertEqual([lesson["id"] for lesson in self.rows()["a"]["lessons"]], ["1.2"])

    def test_legacy_project_binding_can_be_recovered_from_original_scan(self):
        self.add_bb_thread("b")
        self.log_review(self.scan())
        legacy = self.events()[0]
        legacy.pop("project", None)
        self.log_path.write_text(json.dumps(legacy) + "\n")
        before = self.log_path.read_bytes()
        self.correction(self.lesson("project_decision", scope="project"))
        self.assertTrue(self.log_path.read_bytes().startswith(before))
        active = self.rows()["b"]["lessons"]
        self.assertEqual([lesson["id"] for lesson in active], ["1.1"])
        self.assertEqual(active[0]["target"], "p")

    def test_invalid_end_requests_do_not_append_or_change_active_lessons(self):
        self.log_review(self.scan())
        self.correction(self.lesson())
        before = self.log_path.read_bytes()
        for lesson_id, evidence in (("9.9", "Release the hold."), ("bad", "Release the hold."), ("1.1", " \n")):
            with self.subTest(id=lesson_id, evidence=evidence):
                result = self.cli("log.py", "end-lesson", "--id", lesson_id, "--evidence", evidence, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.log_path.read_bytes(), before)
        self.assertEqual([lesson["id"] for lesson in self.rows()["a"]["lessons"]], ["1.1"])

    def test_cmux_lesson_follows_session_move_and_excludes_replacement(self):
        self.cmux_fixture()
        first = self.scan(env=self.cmux_env())
        self.log_review(first, picked="STOPPED")
        self.correction(self.lesson("exception"))
        original = self.rows(env=self.cmux_env())["STOPPED"]["lessons"][0]
        self.assertEqual(original["target"], "cmux:claude:s1")
        data = self.fixture["cmux"]
        moved = {**data["sessions"][1], "surface_id": "MOVED"}
        data["sessions"][1]["session_id"] = "replacement-conversation"
        data["sessions"].append(moved)
        data["tree"]["windows"][0]["workspaces"][0]["panes"][0]["surfaces"].append(
            {"id": "MOVED", "type": "terminal"})
        data["screens"]["MOVED"] = data["screens"]["STOPPED"]
        self.save_fixture()
        rows = self.rows(env=self.cmux_env())
        self.assertEqual([lesson["id"] for lesson in rows["MOVED"]["lessons"]], ["1.1"])
        self.assertEqual(rows["STOPPED"]["lessons"], [])
        self.assertEqual(rows["MOVED"]["history"]["pick_count"], 1)
        self.assertEqual(rows["STOPPED"]["history"]["pick_count"], 0)
        self.assertNotIn("bb", self.called())

    def test_cmux_project_scope_matches_exact_project_path(self):
        self.cmux_fixture()
        data = self.fixture["cmux"]
        for surface, session_id, cwd in (("SAME", "s4", "/repo"), ("OTHER", "s5", "/repo-other")):
            data["sessions"].append({**data["sessions"][1], "surface_id": surface,
                                     "session_id": session_id, "cwd": cwd})
            data["tree"]["windows"][0]["workspaces"][0]["panes"][0]["surfaces"].append(
                {"id": surface, "type": "terminal"})
            data["screens"][surface] = "Tests pass."
        self.save_fixture()
        self.log_review(self.scan(env=self.cmux_env()), picked="STOPPED")
        self.correction(self.lesson("project_decision", scope="project"))
        rows = self.rows(env=self.cmux_env())
        self.assertEqual([lesson["id"] for lesson in rows["SAME"]["lessons"]], ["1.1"])
        self.assertEqual(rows["OTHER"]["lessons"], [])
        self.assertNotIn("bb", self.called())


if __name__ == "__main__":
    unittest.main()
