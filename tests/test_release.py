import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

from scripts.build_release import build_release, INSTRUCTION_FILES, RUNTIME_FILES, TEMPLATE_FILES


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        # Synthetic source avoids copying private files or running any app CLI.
        for relative in RUNTIME_FILES + INSTRUCTION_FILES + TEMPLATE_FILES:
            self.write(relative, f"Public runtime: {relative}\n")
        self.write("install.sh", "#!/bin/sh\nexit 0\n")
        (self.source / "CLAUDE.md").symlink_to("AGENTS.md")
        (self.source / "director/CLAUDE.md").symlink_to("AGENTS.md")
        (self.source / ".claude").mkdir()
        (self.source / ".claude/skills").symlink_to("../.agents/skills")
        self.output = self.root / "release"

    def write(self, relative, text):
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def build(self, **kwargs):
        return build_release(self.source, kwargs.get("version", "v0.1.0"),
                             kwargs.get("output", self.output))

    def test_archive_has_runtime_instructions_version_and_contained_links(self):
        self.build()
        with tarfile.open(self.output / "director-v0.1.0.tar.gz", "r:gz") as archive:
            members = archive.getmembers()
            names = {member.name for member in members}
            for expected in (
                "director/director/cli.py", "director/director/install.py",
                "director/director/launch.py", "director/director/lifecycle.py",
                "director/director/scan.py", "director/ROLE.md",
                "director/LICENSE",
                "director/docs/memory.md",
                "director/docs/profile.md",
                "director/docs/plugins.md",
                "director/director/plugins.py",
                "director/director/viewer/server.py",
                "director/director/viewer/inline.py",
                "director/.agents/skills/director-teachings/SKILL.md",
                "director/director/viewer/reader.py",
                "director/director/viewer/index.html",
                "director/director/viewer/app.js",
                "director/director/viewer/styles.css",
                "director/docs/viewer.md",
                "director/.agents/skills/director-bb/SKILL.md",
                "director/.agents/skills/director-cmux/SKILL.md",
            ):
                self.assertIn(expected, names)
            self.assertEqual(archive.extractfile("director/VERSION").read(), b"v0.1.0\n")
            for relative in TEMPLATE_FILES:
                self.assertEqual(archive.extractfile("director/" + relative).read(),
                                 (self.source / relative).read_bytes())
            self.assertEqual(archive.getmember("director/CLAUDE.md").linkname, "AGENTS.md")
            self.assertEqual(archive.getmember("director/.claude/skills").linkname,
                             "../.agents/skills")
            for member in members:
                self.assertEqual(Path(member.name).parts[0], "director")
                self.assertNotIn("..", Path(member.name).parts)
                self.assertFalse(Path(member.name).is_absolute())
                self.assertEqual((member.uid, member.gid, member.mtime), (0, 0, 0))
                self.assertEqual((member.uname, member.gname), ("", ""))

    def test_private_files_credentials_and_unlisted_code_are_excluded(self):
        secret = "PRIVATE-CREDENTIAL-MARKER"
        for relative in (
            "private/log.jsonl", "private/judgment/what.md", "profile/qa.md",
            "profile/settings.json", "profile/lessons.jsonl", "profile/plugins.json",
            "private/todoist-plugin/plugin.py", "state/plugins/example/run.json", "state/config.json", "state/log.jsonl", ".env", ".env.production",
            ".git/config", "director/.env", "director/credentials.py",
            "director/__pycache__/scan.pyc", "tests/test_secret.py", ".ssh/id_rsa",
            ".agents/skills/director-bb/.env", ".agents/skills/other/SKILL.md",
            "templates/profile/.env", "templates/profile/settings.json",
            "templates/profile/lessons.jsonl", "templates/profile/AGENTS.md",
            "templates/profile/private/notes.md", "templates/other/qa.md",
        ):
            self.write(relative, secret)
        self.build()
        with tarfile.open(self.output / "director-v0.1.0.tar.gz", "r:gz") as archive:
            for member in archive.getmembers():
                if member.isfile():
                    self.assertNotIn(secret.encode(), archive.extractfile(member).read())
            self.assertFalse(any(len(Path(name).parts) > 1 and Path(name).parts[1] in
                                 {"profile", "state", "private"} for name in archive.getnames()))
            self.assertEqual({member.name for member in archive.getmembers()
                              if member.isfile() and member.name.startswith("director/templates/")},
                             {"director/" + relative for relative in TEMPLATE_FILES})
            self.assertNotIn("director/director/credentials.py", archive.getnames())
            self.assertNotIn("director/tests/test_secret.py", archive.getnames())

    def test_reproducible_when_source_metadata_and_output_path_change(self):
        self.build()
        for path in self.source.rglob("*"):
            if path.is_file() and not path.is_symlink():
                os.utime(path, (987654321, 987654321))
                path.chmod(0o755)
        second = self.root / "second-release"
        self.build(output=second)
        for name in ("director-v0.1.0.tar.gz", "SHA256SUMS", "install.sh"):
            self.assertEqual((self.output / name).read_bytes(), (second / name).read_bytes())

    def test_checksums_cover_archive_and_exact_published_installer(self):
        self.build()
        self.assertEqual({path.name for path in self.output.iterdir()},
                         {"director-v0.1.0.tar.gz", "SHA256SUMS", "install.sh"})
        lines = (self.output / "SHA256SUMS").read_text().splitlines()
        self.assertEqual(len(lines), 2)
        for line in lines:
            digest, name = line.split("  ")
            self.assertEqual(digest, hashlib.sha256((self.output / name).read_bytes()).hexdigest())
        self.assertEqual((self.output / "install.sh").read_bytes(),
                         (self.source / "install.sh").read_bytes())

    def test_invalid_versions_leave_no_output(self):
        for version in ("", "0.1.0", "v01.0.0", "v0.1", "v0.1.0-rc.1", "v0.1.0/../x",
                        "v0.1.0\n", "v0.1.0;echo bad", "v1.2.3+build"):
            with self.subTest(version=version):
                with self.assertRaisesRegex(ValueError, "stable tag"):
                    self.build(version=version)
                self.assertFalse(self.output.exists())

    def test_reused_output_preserves_existing_assets(self):
        self.build()
        before = {path.name: path.read_bytes() for path in self.output.iterdir()}
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.build(version="v0.2.0")
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.output.iterdir()})

    def test_existing_empty_output_and_output_symlink_are_rejected(self):
        self.output.mkdir()
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.build()
        self.output.rmdir()
        self.output.symlink_to(self.root / "missing")
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.build()
        self.assertFalse((self.root / "missing").exists())

    def test_missing_runtime_file_does_not_publish_partial_output(self):
        (self.source / "director/cli.py").unlink()
        with self.assertRaises(FileNotFoundError):
            self.build()
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob(".director-release-*")))

    def test_missing_template_does_not_publish_partial_output(self):
        (self.source / "templates/profile/qa.md").unlink()
        with self.assertRaises(FileNotFoundError):
            self.build()
        self.assertFalse(self.output.exists())

    def test_template_symlink_cannot_package_personal_teaching(self):
        teaching = self.write("profile/qa.md", "Private teaching must stay local.\n")
        path = self.source / "templates/profile/qa.md"
        path.unlink()
        path.symlink_to(teaching)
        with self.assertRaisesRegex(ValueError, "source symlinks"):
            self.build()
        self.assertFalse(self.output.exists())

    def test_outbound_source_file_symlink_cannot_package_secrets(self):
        secret = self.root / "credential"
        secret.write_text("secret")
        path = self.source / "director/cli.py"
        path.unlink()
        path.symlink_to(secret)
        with self.assertRaisesRegex(ValueError, "source symlinks"):
            self.build()
        self.assertFalse(self.output.exists())

    def test_symlinked_source_parent_cannot_package_secrets(self):
        original = self.source / ".agents"
        relocated = self.root / "external-skills"
        original.rename(relocated)
        original.symlink_to(relocated, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "source symlinks"):
            self.build()
        self.assertFalse(self.output.exists())

    def test_known_link_must_match_its_contained_target(self):
        for relative in ("CLAUDE.md", "director/CLAUDE.md", ".claude/skills"):
            with self.subTest(relative=relative):
                link = self.source / relative
                old_target = link.readlink()
                link.unlink()
                link.symlink_to("/etc/passwd")
                with self.assertRaisesRegex(ValueError, "expected source symlink"):
                    self.build()
                self.assertFalse(self.output.exists())
                link.unlink()
                link.symlink_to(old_target)

    def test_unlisted_symlinks_are_never_followed(self):
        (self.source / "private").symlink_to(self.root / "does-not-exist")
        (self.source / "director/secrets.py").symlink_to("/etc/passwd")
        self.build()
        with tarfile.open(self.output / "director-v0.1.0.tar.gz", "r:gz") as archive:
            self.assertNotIn("director/private", archive.getnames())
            self.assertNotIn("director/director/secrets.py", archive.getnames())

    def test_installer_symlink_is_rejected(self):
        path = self.source / "install.sh"
        path.unlink()
        path.symlink_to("/etc/passwd")
        with self.assertRaisesRegex(ValueError, "source symlinks"):
            self.build()
        self.assertFalse(self.output.exists())

    def test_cli_reports_bad_version_without_traceback(self):
        command = Path(__file__).resolve().parents[1] / "scripts/build_release.py"
        result = subprocess.run(
            [sys.executable, str(command), "--version", "bad", "--output", str(self.output)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("stable tag", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
