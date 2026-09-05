import copy
import json
import time
import unittest

import test_cli


class OutcomeTests(unittest.TestCase):
    def setUp(self):
        self.case = test_cli.CliTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def proposal(self, cmux=False):
        if cmux:
            self.case.cmux_fixture()
        env = self.case.cmux_env() if cmux else self.case.env
        scan = self.case.scan(env=env)
        picked = "STOPPED" if cmux else "a"
        self.case.cli("log.py", "run", "--picked", picked, "--decision", "unblock",
                      "--seen", "blocked", "--reason", "obvious next step",
                      "--proposed-action", "CONTINUE", "--scan", scan["scan"], env=env)
        return scan

    def outcome(self, status, *args, **kwargs):
        return self.case.cli("log.py", "outcome", "--run", "1", "--status", status,
                             "--detail", "observed command result", *args, **kwargs)

    def records(self):
        return [json.loads(line) for line in self.case.log_path.read_text().splitlines()]

    def test_unsent_proposal_is_not_a_confirmed_resume(self):
        self.proposal()
        row = self.records()[0]
        self.assertEqual(row["proposed_action"], "CONTINUE")
        self.assertIsNone(row["action"])
        stats = self.case.cli("log.py", "stats").stdout
        self.assertIn("decisions:", stats)
        self.assertIn("proposals 1", stats)
        self.assertIn("confirmed_resumes 0", stats)

    def test_queued_action_stays_unconfirmed_while_idle(self):
        self.proposal()
        self.outcome("queued")
        result = self.case.cli("log.py", "confirm", "--run", "1")
        self.assertIn("not confirmed", result.stdout)
        self.assertEqual(self.records()[-1]["status"], "queued")
        stats = self.case.cli("log.py", "stats").stdout
        self.assertIn("queued 1", stats)
        self.assertIn("confirmed_resumes 0", stats)
        calls = [json.loads(line) for line in self.case.calls.read_text().splitlines()]
        self.assertFalse(any("tell" in call or "retry" in call for call in calls))

    def test_queued_delivery_keeps_the_actual_approved_action(self):
        self.proposal()
        self.outcome("queued", "--action", "CONTINUE WITH THE TESTS")
        self.outcome("sent")
        self.assertEqual(self.records()[-1]["action"], "CONTINUE WITH THE TESTS")
        self.assertEqual(self.records()[0]["proposed_action"], "CONTINUE")

    def test_running_observation_confirms_once_without_new_review(self):
        self.proposal()
        before = self.case.log_path.read_bytes()
        self.outcome("sent", "--action", "CONTINUE WITH THE TESTS")
        self.case.fixture["bb"]["threads"][0]["status"] = "active"
        self.case.save_fixture()
        self.case.cli("log.py", "confirm", "--run", "1")
        self.assertTrue(self.case.log_path.read_bytes().startswith(before))
        row = self.records()[-1]
        self.assertEqual(row["status"], "resumed")
        self.assertEqual(row["observation"]["status"], "active")
        confirmed = self.case.log_path.read_bytes()
        self.case.cli("log.py", "confirm", "--run", "1")
        self.assertEqual(self.case.log_path.read_bytes(), confirmed)
        stats = self.case.cli("log.py", "stats").stdout
        self.assertIn("runs 1", stats)
        self.assertIn("confirmed_resumes 1", stats)
        self.case.fixture["bb"]["threads"][0]["status"] = "idle"
        self.case.save_fixture()
        history = self.case.scan()["candidates"][0]["history"]
        self.assertEqual(history["pick_count"], 1)
        self.assertEqual(history["last_review_at"], self.records()[0]["reviewed_at"])

    def test_resume_cannot_be_claimed_without_observation(self):
        self.proposal()
        before = self.case.log_path.read_bytes()
        result = self.outcome("resumed", check=False)
        self.assertNotEqual(result.returncode, 0)
        result = self.case.cli("log.py", "confirm", "--run", "1", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.case.log_path.read_bytes(), before)

    def test_failed_and_skipped_actions_cannot_be_confirmed(self):
        self.proposal()
        before = self.case.log_path.read_bytes()
        for status in ("failed", "skipped"):
            with self.subTest(status=status):
                self.case.log_path.write_bytes(before)
                self.outcome(status)
                self.case.fixture["bb"]["threads"][0]["status"] = "active"
                self.case.save_fixture()
                result = self.case.cli("log.py", "confirm", "--run", "1", check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("confirmed_resumes 0", self.case.cli("log.py", "stats").stdout)

    def test_skip_after_recent_human_input_keeps_original_review(self):
        original = self.proposal()
        self.case.fixture["bb"]["events"]["a"].append({"id": "human", "seq": 2,
            "createdAt": int(time.time() * 1000), "type": "client/turn/requested", "data": {"initiator": "user"}})
        self.case.save_fixture()
        recheck = self.case.scan()
        self.assertEqual(recheck["skipped_user_recent"], ["a"])
        self.outcome("skipped", "--scan", recheck["scan"])
        rows = self.records()
        self.assertEqual(rows[0]["scan"], original["scan"])
        self.assertEqual(rows[-1]["scan"], recheck["scan"])
        self.assertEqual(rows[-1]["status"], "skipped")
        self.assertIn("runs 1", self.case.cli("log.py", "stats").stdout)

    def test_skip_after_agent_starts_is_recorded(self):
        self.proposal()
        self.case.fixture["bb"]["threads"][0]["status"] = "active"
        self.case.save_fixture()
        recheck = self.case.scan()
        self.assertEqual(recheck["candidates"], [])
        self.outcome("skipped", "--scan", recheck["scan"])
        self.assertEqual(self.records()[-1]["status"], "skipped")

    def test_legacy_action_text_does_not_prove_a_resume(self):
        self.case.log_review(self.case.scan())
        legacy = self.records()[0]
        legacy.pop("action_status", None)
        legacy.pop("proposed_action", None)
        legacy.update(decision="unblock", action="CONTINUE")
        original = (json.dumps(legacy) + "\n").encode()
        self.case.log_path.write_bytes(original)
        self.case.log_review(self.case.scan())
        self.assertTrue(self.case.log_path.read_bytes().startswith(original))
        stats = self.case.cli("log.py", "stats").stdout
        self.assertIn("legacy_unknown 1", stats)
        self.assertIn("confirmed_resumes 0", stats)

    def test_cmux_confirmation_follows_session_not_reused_terminal(self):
        self.proposal(cmux=True)
        self.outcome("sent")
        data = self.case.fixture["cmux"]
        original = dict(data["sessions"][1])
        data["sessions"][1].update(session_id="different", agent_lifecycle="running")
        self.case.save_fixture()
        self.case.cli("log.py", "confirm", "--run", "1", env=self.case.cmux_env())
        self.assertIn("confirmed_resumes 0", self.case.cli("log.py", "stats").stdout)
        data["sessions"].append({**original, "surface_id": "MOVED", "agent_lifecycle": "running"})
        data["tree"]["windows"][0]["workspaces"][0]["panes"][0]["surfaces"].append(
            {"id": "MOVED", "type": "terminal"})
        self.case.save_fixture()
        self.case.cli("log.py", "confirm", "--run", "1", env=self.case.cmux_env())
        self.assertEqual(self.records()[-1]["status"], "resumed")
        self.assertEqual(self.records()[-1]["observation"]["id"], "MOVED")
        self.assertNotIn("bb", self.case.called())

    def test_confirmation_cannot_read_the_other_app(self):
        self.proposal()
        self.outcome("sent")
        before_calls = self.case.calls.read_bytes()
        result = self.case.cli("log.py", "confirm", "--run", "1", env=self.case.cmux_env(), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.case.calls.read_bytes(), before_calls)

    def test_cmux_exited_or_closed_agent_cannot_be_confirmed(self):
        self.proposal(cmux=True)
        self.outcome("sent")
        baseline = copy.deepcopy(self.case.fixture)
        for gone in ("exited", "closed"):
            with self.subTest(gone=gone):
                self.case.fixture = copy.deepcopy(baseline)
                data = self.case.fixture["cmux"]
                data["sessions"][1]["agent_lifecycle"] = "running"
                if gone == "exited":
                    data["sessions"][1]["stored_pid_exists"] = False
                else:
                    data["tree"]["windows"] = []
                self.case.save_fixture()
                self.case.cli("log.py", "confirm", "--run", "1", env=self.case.cmux_env())
                self.assertEqual(self.records()[-1]["status"], "sent")
                self.assertIn("confirmed_resumes 0", self.case.cli("log.py", "stats").stdout)

    def test_unreachable_app_leaves_action_unconfirmed_with_error(self):
        self.proposal()
        self.outcome("sent")
        self.case.fixture = {}
        self.case.save_fixture()
        result = self.case.cli("log.py", "confirm", "--run", "1")
        self.assertIn("not confirmed", result.stdout)
        self.assertEqual(self.records()[-1]["status"], "sent")
        self.assertIn("error", self.records()[-1]["observation"])
        self.assertIn("confirmed_resumes 0", self.case.cli("log.py", "stats").stdout)


if __name__ == "__main__":
    unittest.main()
