from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPTS = ("scan.py", "log.py", "memory.py", "bb_app.py", "cmux_app.py")
FAKE = f"#!{sys.executable}\n" + '''import json, os, sys
from pathlib import Path
app = Path(sys.argv[0]).name
with open(os.environ["FAKE_CALLS"], "a") as f:
    f.write(json.dumps([app, *sys.argv[1:]]) + "\\n")
data = json.loads(Path(os.environ["FAKE_FIXTURE"]).read_text()).get(app)
if data is None:
    sys.exit(app + " must not be called by this Director")
args = [a for a in sys.argv[1:] if a != "--json"]
if app == "bb" and args == ["status"]:
    result = {"thread": {"environment": {"hostId": "host"}}}
elif app == "bb" and args == ["thread", "list"]:
    result = data["threads"]
elif app == "bb" and args[:2] == ["thread", "log"]:
    result = data["events"][args[2]]
elif app == "cmux" and args == ["sessions", "list", "--all"]:
    result = {"state_dir": str(Path(os.environ["FAKE_FIXTURE"]).parent), "sessions": data["sessions"]}
elif app == "cmux" and args == ["list-workspaces", "--id-format", "both"]:
    result = {"workspaces": data["workspaces"]}
elif app == "cmux" and args[:2] == ["read-screen", "--surface"]:
    sys.exit(print(data["screens"][args[2]]))
else:
    sys.exit("unexpected " + app + " action: " + repr(args))
print(json.dumps(result))
'''


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in SCRIPTS:
            shutil.copy(Path(__file__).with_name(name), self.root / name)
        for app in ("bb", "cmux"):
            (self.root / app).write_text(FAKE)
            (self.root / app).chmod(0o755)
        self.calls = self.root / "calls.jsonl"
        clean = {k: v for k, v in os.environ.items() if not k.startswith(("BB_", "CMUX_"))}
        self.env = {**clean, "PATH": str(self.root) + os.pathsep + os.environ["PATH"],
                    "FAKE_FIXTURE": str(self.root / "fixture.json"), "FAKE_CALLS": str(self.calls),
                    "BB_THREAD_ID": "self"}
        now = int(time.time() * 1000)
        self.fixture = {"bb": {"threads": [{"id": "a", "projectId": "p", "status": "idle", "title": "example",
                                           "environmentHostId": "host", "updatedAt": now}],
                               "events": {"a": [{"id": "1", "seq": 1, "createdAt": now, "type": "item/completed",
                                                 "data": {"item": {"type": "agentMessage", "text": "done"}}}]}}}
        self.save_fixture()

    def save_fixture(self):
        (self.root / "fixture.json").write_text(json.dumps(self.fixture))

    def called(self):
        return [json.loads(line)[0] for line in self.calls.read_text().splitlines()] if self.calls.exists() else []

    def cli(self, script, *args, check=True, env=None):
        return subprocess.run([sys.executable, str(self.root / script), *args], env=env or self.env,
                              cwd=self.root, capture_output=True, text=True, check=check)

    def scan(self, *args, **kw):
        return json.loads(self.cli("scan.py", "--seed", "1", *args, **kw).stdout)

    def log_review(self, scan=None, picked="a"):
        args = ["run", "--picked", picked, "--decision", "leave", "--seen", "finished", "--reason", "complete"]
        if scan:
            args.extend(["--scan", scan["scan"]])
        return self.cli("log.py", *args)

    @property
    def log_path(self):
        return self.root / "private" / "log.jsonl"

    def test_scan_review_noise_and_meaningful_change(self):
        first = self.scan()
        self.assertEqual(first["app"], "bb")
        self.assertEqual(first["selection"]["mode"], "priority")
        self.assertEqual(first["selection"]["chance"], 0)
        self.assertFalse(any(row["spot_check"] for row in first["candidates"]))
        self.assertEqual(first["coverage"], "full")
        self.assertTrue((self.root / "private" / first["scan"]).exists())
        self.log_review(first)
        stored = json.loads(self.log_path.read_text())
        self.assertEqual(stored["app"], "bb")
        self.assertEqual(stored["reviewed_state"], first["candidates"][0]["state"])
        self.assertEqual(stored["scan_selection"], first["selection"])
        second = self.scan()
        self.assertNotEqual(first["scan"], second["scan"])
        self.assertEqual(second["selection"]["mode"], "none")
        self.assertEqual(second["candidates"][0]["eligibility_reason"], "leave_cooldown")
        self.fixture["bb"]["events"]["a"].append({"id": "2", "seq": 2, "createdAt": int(time.time() * 1000),
                                                   "type": "thread/contextWindowUsage/updated", "data": {"tokens": 5}})
        self.save_fixture()
        self.assertFalse(self.scan()["candidates"][0]["review_eligible"])
        self.fixture["bb"]["events"]["a"].append({"id": "3", "seq": 3, "createdAt": int(time.time() * 1000),
                                                   "type": "provider/error", "data": {"message": "offline"}})
        self.save_fixture()
        changed = self.scan()
        self.assertEqual(changed["candidates"][0]["eligibility_reason"], "state_changed")
        self.assertEqual(changed["selection"]["suggested"], "a")
        self.assertNotIn("cmux", self.called())

    def test_correction_appends_and_scan_uses_it(self):
        self.log_review(self.scan())
        before = self.log_path.read_bytes()
        self.cli("log.py", "override", "--run", "1", "--david", "continue", "--decision", "unblock")
        self.assertTrue(self.log_path.read_bytes().startswith(before))
        corrected = self.scan()["candidates"][0]
        self.assertEqual(corrected["history"]["last_decision"], "unblock")
        self.assertEqual(corrected["history"]["pick_count"], 1)
        self.assertTrue(corrected["review_eligible"])
        self.assertIn("unblock 1", self.cli("log.py", "stats").stdout)

    def test_unmatched_scan_does_not_record_review(self):
        result = self.cli("log.py", "run", "--picked", "missing", "--decision", "leave", "--seen", "x",
                          "--reason", "x", "--scan", self.scan()["scan"], check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log_path.exists())

    def test_concurrent_reviews_get_distinct_run_numbers(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda n: self.log_review(picked=str(n)), range(8)))
        rows = [json.loads(line) for line in self.log_path.read_text().splitlines()]
        self.assertEqual([r["run"] for r in rows], list(range(1, 9)))
        self.assertEqual({r["picked"] for r in rows}, {str(n) for n in range(8)})

    def test_legacy_log_without_final_newline_stays_readable(self):
        self.log_review()
        self.log_path.write_text(self.log_path.read_text().rstrip())
        self.log_review()
        self.assertIn("runs 2", self.cli("log.py", "stats").stdout)

    def test_corrupt_history_blocks_scan_and_append(self):
        self.log_path.parent.mkdir()
        self.log_path.write_text('{"run":')
        self.assertNotEqual(self.cli("scan.py", check=False).returncode, 0)
        result = self.cli("log.py", "run", "--picked", "a", "--decision", "leave", "--seen", "x", "--reason", "x", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.log_path.read_text(), '{"run":')

    # App isolation: Director watches only the app it was launched in.

    def cmux_env(self):
        env = {k: v for k, v in self.env.items() if k != "BB_THREAD_ID"}
        return {**env, "CMUX_WORKSPACE_ID": "WS-SELF", "CMUX_SURFACE_ID": "SELF"}

    def cmux_fixture(self):
        now = time.time()
        events = self.root / "events.jsonl"
        events.write_text(json.dumps({"name": "agent.hook.UserPromptSubmit", "surface_id": "FRESH",
                                      "occurred_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))}) + "\n")
        session = {"agent": "claude", "agent_display_name": "Claude Code", "agent_lifecycle": "idle", "cwd": "/repo",
                   "pid": 1, "stored_pid_exists": True, "active_for_surface": True, "workspace_id": "WS-1",
                   "updated_at_unix": now - 900}
        self.fixture = {"cmux": {
            "sessions": [{**session, "surface_id": "SELF", "session_id": "s0"},
                         {**session, "surface_id": "STOPPED", "session_id": "s1"},
                         {**session, "surface_id": "FRESH", "session_id": "s2"},
                         {**session, "surface_id": "BUSY", "session_id": "s3", "agent_lifecycle": "running"}],
            "workspaces": [{"workspace_id": "ws-1", "workspace_ref": "workspace:1", "title": "demo"}],
            "screens": {"STOPPED": "│ Tests pass. Ship it?\n❯ ", "FRESH": "working"}}}
        self.save_fixture()

    def test_cmux_director_scans_cmux_only(self):
        self.cmux_fixture()
        first = self.scan(env=self.cmux_env())
        self.assertEqual(first["app"], "cmux")
        self.assertEqual(first["self"], "SELF")
        self.assertEqual([r["id"] for r in first["candidates"]], ["STOPPED"])
        self.assertEqual(first["skipped_user_recent"], ["FRESH"])
        self.assertEqual(first["dropped"], {"inactive": 0, "self": 1, "closed": 0, "gone": 0, "running": 1, "old": 0})
        self.assertEqual(first["selection"]["suggested"], "STOPPED")
        self.assertTrue(first["candidates"][0]["ends_with_question"])
        self.assertEqual(first["candidates"][0]["title"], "demo · Claude Code")
        self.assertEqual(first["coverage"], "full")
        self.assertNotIn("bb", self.called())
        self.log_review(first, picked="STOPPED")
        stored = json.loads(self.log_path.read_text())
        self.assertEqual(stored["app"], "cmux")
        second = self.scan(env=self.cmux_env())
        self.assertEqual(second["candidates"][0]["eligibility_reason"], "leave_cooldown")
        self.assertEqual(second["selection"]["mode"], "none")
        self.fixture["cmux"]["screens"]["STOPPED"] += "\nError: tests failed"
        self.save_fixture()
        self.assertEqual(self.scan(env=self.cmux_env())["candidates"][0]["eligibility_reason"], "state_changed")
        self.assertNotIn("bb", self.called())

    def test_cmux_scan_fails_loudly_when_the_app_is_not_reachable(self):
        self.cmux_fixture()
        del self.fixture["cmux"]["workspaces"]
        self.save_fixture()
        result = self.cli("scan.py", check=False, env=self.cmux_env())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("list-workspaces", result.stderr)
        self.assertFalse((self.root / "private" / "scans").exists())

    def test_launch_app_must_be_unambiguous(self):
        neither = {k: v for k, v in self.env.items() if k != "BB_THREAD_ID"}
        both = {**self.cmux_env(), "BB_THREAD_ID": "self"}
        for env in (neither, both):
            result = self.cli("scan.py", check=False, env=env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("launch app", result.stderr)
        self.assertEqual(self.called(), [])
        self.cmux_fixture()
        self.assertEqual(self.scan("--app", "cmux", env=both)["app"], "cmux")
        self.assertNotIn("bb", self.called())

    def test_app_override_cannot_watch_an_app_it_was_not_launched_in(self):
        result = self.cli("scan.py", "--app", "cmux", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CMUX_SURFACE_ID is not set", result.stderr)
        self.assertEqual(self.called(), [])


if __name__ == "__main__":
    unittest.main()
