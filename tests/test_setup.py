import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(sys.platform == "darwin", "setup supports macOS")
class SetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        scripts = self.root / "director"
        scripts.mkdir()
        for name in ("setup.py", "cmux_app.py", "storage.py", "migrate.py", "preferences.py", "lessons.py", "memory.py"):
            shutil.copy(Path(__file__).resolve().parents[1] / "director" / name, scripts / name)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("BB_", "CMUX_"))}
        self.env["PATH"] = str(self.root)

    def tool(self, app):
        path = self.root / app
        path.write_text("#!/bin/sh\n: > \"$0.called\"\nexit 1\n")
        path.chmod(0o755)

    def setup(self, app):
        return subprocess.run([sys.executable, str(self.root / "director" / "setup.py"), "--app", app],
                              cwd=self.root.parent, env=self.env, capture_output=True, text=True)

    def test_fresh_setup_and_rerun_preserve_profile_and_state(self):
        self.tool("bb")
        first = self.setup("bb")
        self.assertEqual(first.returncode, 0, first.stderr)
        judgment = self.root / "profile"
        self.assertEqual({p.name for p in judgment.glob("*.md")}, {"what.md", "how.md", "limits.md", "qa.md"})
        self.assertIn("Ask me before every message", (judgment / "limits.md").read_text())
        settings = judgment / "settings.json"
        self.assertEqual(json.loads(settings.read_text())["legacy_override_decisions"], {})
        settings.write_text('{"recheck_seconds": 900}\n')
        for path in judgment.glob("*.md"):
            path.write_text("My existing rules and answers.\n")
        log = self.root / "state" / "log.jsonl"
        history = json.dumps({"run": 1, "decision": "leave", "picked": "bb:thread-a", "ts": "2026-09-01T10:00:00+00:00"}) + "\n"
        log.write_text(history)
        second = self.setup("bb")
        self.assertEqual(second.returncode, 0, second.stderr)
        for path in judgment.glob("*.md"):
            self.assertEqual(path.read_text(), "My existing rules and answers.\n")
        self.assertEqual(log.read_text(), history)
        self.assertEqual(settings.read_text(), '{"recheck_seconds": 900}\n')
        self.assertFalse((self.root / "bb.called").exists())

    def test_missing_bb_reports_fix_without_creating_rules(self):
        result = self.setup("bb")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Missing bb CLI", result.stderr)
        self.assertIn("PATH", result.stderr)
        self.assertFalse((self.root / "private").exists())

    def test_setup_migrates_only_director_files_and_preserves_private_notes(self):
        self.tool("bb")
        legacy = self.root / "private"
        (legacy / "judgment").mkdir(parents=True)
        (legacy / "judgment/qa.md").write_text("Q01: Keep my exact words.\n")
        (legacy / "log.jsonl").write_text('{"run": 7, "decision": "leave", "picked": "bb:thread-a", "ts": "2026-09-01T10:00:00+00:00"}\n')
        note = legacy / "brief.md"
        note.write_text("Unrelated private notes.\n")
        result = self.setup("bb")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "profile/qa.md").read_text(), "Q01: Keep my exact words.\n")
        self.assertEqual((self.root / "state/log.jsonl").read_text(), '{"run": 7, "decision": "leave", "picked": "bb:thread-a", "ts": "2026-09-01T10:00:00+00:00"}\n')
        self.assertEqual(note.read_text(), "Unrelated private notes.\n")
        self.assertEqual((legacy / "judgment/qa.md").resolve(), (self.root / "profile/qa.md").resolve())
        self.assertFalse((self.root / "bb.called").exists())

    def test_cmux_setup_needs_only_cmux_and_does_not_run_hooks(self):
        self.tool("cmux")
        result = self.setup("cmux")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("cmux hooks setup", result.stdout)
        self.assertTrue((self.root / "profile" / "limits.md").is_file())
        self.assertFalse((self.root / "cmux.called").exists())


if __name__ == "__main__":
    unittest.main()
