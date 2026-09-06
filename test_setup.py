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
        for name in ("setup.py", "cmux_app.py"):
            shutil.copy(Path(__file__).with_name(name), self.root / name)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("BB_", "CMUX_"))}
        self.env["PATH"] = str(self.root)

    def tool(self, app):
        path = self.root / app
        path.write_text("#!/bin/sh\n: > \"$0.called\"\nexit 1\n")
        path.chmod(0o755)

    def setup(self, app):
        return subprocess.run([sys.executable, str(self.root / "setup.py"), "--app", app],
                              cwd=self.root.parent, env=self.env, capture_output=True, text=True)

    def test_fresh_setup_and_rerun_preserve_private_data(self):
        self.tool("bb")
        first = self.setup("bb")
        self.assertEqual(first.returncode, 0, first.stderr)
        judgment = self.root / "private" / "judgment"
        self.assertEqual({p.name for p in judgment.iterdir()}, {"what.md", "how.md", "limits.md", "qa.md"})
        self.assertIn("Ask me before every message", (judgment / "limits.md").read_text())
        for path in judgment.iterdir():
            path.write_text("My existing rules and answers.\n")
        log = self.root / "private" / "log.jsonl"
        log.write_text("existing history\n")
        second = self.setup("bb")
        self.assertEqual(second.returncode, 0, second.stderr)
        for path in judgment.iterdir():
            self.assertEqual(path.read_text(), "My existing rules and answers.\n")
        self.assertEqual(log.read_text(), "existing history\n")
        self.assertFalse((self.root / "bb.called").exists())

    def test_missing_bb_reports_fix_without_creating_rules(self):
        result = self.setup("bb")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Missing bb CLI", result.stderr)
        self.assertIn("PATH", result.stderr)
        self.assertFalse((self.root / "private").exists())

    def test_cmux_setup_needs_only_cmux_and_does_not_run_hooks(self):
        self.tool("cmux")
        result = self.setup("cmux")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("cmux hooks setup", result.stdout)
        self.assertTrue((self.root / "private" / "judgment" / "limits.md").is_file())
        self.assertFalse((self.root / "cmux.called").exists())


if __name__ == "__main__":
    unittest.main()
