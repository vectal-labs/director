"""Scan regressions exercised through fake app CLIs, without real app access."""
import json
import time
import unittest

from tests import test_cli


class ScanSafetyTests(unittest.TestCase):
    def setUp(self):
        self.app = test_cli.CliTests()
        self.app.setUp()
        self.addCleanup(self.app.doCleanups)

    def cmux(self):
        self.app.cmux_fixture()
        return self.app.root / "events.jsonl"

    def cmux_scan(self):
        result = self.app.scan(env=self.app.cmux_env())
        self.assertNotIn("bb", self.app.called())
        return result

    def assert_unknown(self, result):
        self.assertEqual(result["coverage"], "partial")
        self.assertTrue(result["errors"])
        self.assertIsNone(result["selection"]["suggested"])
        self.assertTrue(result["candidates"])
        for row in result["candidates"]:
            self.assertFalse(row["input_history_known"])
            self.assertFalse(row["review_eligible"])
            self.assertEqual(row["eligibility_reason"], "input_history_unknown")

    def test_missing_cmux_input_history_suppresses_suggestions(self):
        self.cmux().unlink()
        self.assert_unknown(self.cmux_scan())

    def test_unreadable_cmux_input_history_suppresses_suggestions(self):
        path = self.cmux()
        path.unlink()
        path.mkdir()
        self.assert_unknown(self.cmux_scan())

    def test_invalid_utf8_cmux_input_history_suppresses_suggestions(self):
        self.cmux().write_bytes(b"\xff")
        self.assert_unknown(self.cmux_scan())

    def test_unattributable_cmux_corruption_preserves_known_recent_input(self):
        for corrupt in ('{"secret":', '[]', '{}',
                        '{"name":"agent.hook.UserPromptSubmit","surface_id":[]}'):
            with self.subTest(corrupt=corrupt):
                path = self.cmux()
                with path.open("a") as stream:
                    stream.write(corrupt + "\n")
                result = self.cmux_scan()
                self.assert_unknown(result)
                self.assertEqual(result["skipped_user_recent"], ["FRESH"])
                self.assertNotIn("secret", json.dumps(result["errors"]))

    def test_bad_cmux_prompt_time_affects_only_its_surface(self):
        for timestamp in (None, [], "secret", "2026-01-01", "2026-01-01T12:00:00", "2026-01-01T12:00:00+25:00"):
            with self.subTest(timestamp=timestamp):
                path = self.cmux()
                path.write_text(json.dumps({"name": "agent.hook.UserPromptSubmit", "surface_id": "STOPPED",
                                            "occurred_at": timestamp}) + "\n")
                result = self.cmux_scan()
                self.assertEqual(result["coverage"], "partial")
                rows = {row["id"]: row for row in result["candidates"]}
                self.assertFalse(rows["STOPPED"]["input_history_known"])
                self.assertFalse(rows["STOPPED"]["review_eligible"])
                self.assertTrue(rows["FRESH"]["input_history_known"])
                self.assertEqual(result["selection"]["suggested"], "FRESH")
                self.assertEqual(result["errors"][0]["id"], "STOPPED")
                self.assertNotIn("secret", json.dumps(result["errors"]))

    def test_cmux_input_submitted_during_screen_read_is_seen(self):
        self.cmux()
        fake = self.app.root / "cmux"
        code = fake.read_text().replace(
            '    sys.exit(print(data["screens"][args[2]]))',
            '\n'.join([
                '    if args[2] == "STOPPED":',
                '        import datetime',
                '        event = {"name": "agent.hook.UserPromptSubmit", "surface_id": "STOPPED",',
                '                 "occurred_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}',
                '        with (Path(os.environ["FAKE_FIXTURE"]).parent / "events.jsonl").open("a") as stream:',
                '            stream.write(json.dumps(event) + "\\n")',
                '    sys.exit(print(data["screens"][args[2]]))']))
        fake.write_text(code)
        result = self.cmux_scan()
        self.assertEqual(result["coverage"], "full")
        self.assertEqual(set(result["skipped_user_recent"]), {"STOPPED", "FRESH"})
        self.assertIsNone(result["selection"]["suggested"])

    def test_bad_cmux_session_does_not_hide_healthy_surfaces(self):
        for invalid in (None, {"surface_id": []}, {"surface_id": "BAD", "active_for_surface": True,
                                                  "updated_at_unix": "secret"}):
            with self.subTest(invalid=invalid):
                self.cmux()
                data = self.app.fixture["cmux"]
                data["sessions"].insert(0, invalid)
                data["tree"]["windows"][0]["workspaces"][0]["panes"][0]["surfaces"].append(
                    {"id": "BAD", "type": "terminal"})
                self.app.save_fixture()
                result = self.cmux_scan()
                self.assertEqual(result["coverage"], "partial")
                self.assertEqual(result["selection"]["suggested"], "STOPPED")
                self.assertEqual([row["id"] for row in result["candidates"]], ["STOPPED"])
                self.assertNotIn("secret", json.dumps(result["errors"]))

    def test_bad_bb_thread_does_not_hide_healthy_threads(self):
        base = self.app.fixture["bb"]["threads"][0]
        for invalid in (None, {"id": "bad"}, {**base, "id": "bad", "updatedAt": "secret"}):
            with self.subTest(invalid=invalid):
                self.app.fixture["bb"]["threads"] = [invalid, base]
                self.app.save_fixture()
                result = self.app.scan()
                self.assertEqual(result["coverage"], "partial")
                self.assertEqual(result["selection"]["suggested"], "a")
                self.assertEqual([row["id"] for row in result["candidates"]], ["a"])
                self.assertNotIn("secret", json.dumps(result["errors"]))
                self.assertNotIn("cmux", self.app.called())

    def test_bad_bb_event_does_not_hide_healthy_threads(self):
        data = self.app.fixture["bb"]
        data["threads"].append({**data["threads"][0], "id": "bad"})
        now = time.time() * 1000
        for invalid in (None, {}, {"type": "client/turn/requested", "data": None, "createdAt": now},
                        {"type": "client/turn/requested", "data": {}, "createdAt": "secret"},
                        {"type": "client/turn/requested", "data": {}, "createdAt": float("nan")},
                        {"type": "item/completed", "data": {"item": []}, "createdAt": now},
                        {"type": "item/completed", "data": {"item": {"type": "agentMessage", "text": None}},
                         "createdAt": now}):
            with self.subTest(invalid=invalid):
                data["events"]["bad"] = [invalid]
                self.app.save_fixture()
                result = self.app.scan()
                self.assertEqual(result["coverage"], "partial")
                self.assertEqual(result["selection"]["suggested"], "a")
                self.assertEqual([row["id"] for row in result["candidates"]], ["a"])
                self.assertEqual(result["errors"][0]["id"], "bad")
                self.assertNotIn("secret", json.dumps(result["errors"]))
                self.assertNotIn("cmux", self.app.called())


if __name__ == "__main__":
    unittest.main()
