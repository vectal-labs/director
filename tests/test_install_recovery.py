"""Recover interrupted local lifecycle operations without touching user apps or data."""
import importlib.util
import json
import os
import shlex
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from unittest.mock import patch

from director import install, launch
from scripts.build_release import build_release


REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("recovery_lifecycle", REPO / "director/lifecycle.py")
lifecycle = importlib.util.module_from_spec(SPEC)
with patch.dict(sys.modules, {"install": install, "launch": launch}):
    SPEC.loader.exec_module(lifecycle)
LEGACY_SPEC = importlib.util.spec_from_file_location("legacy_install", REPO / "tests/fixtures/legacy_install.py")
legacy_install = importlib.util.module_from_spec(LEGACY_SPEC)
LEGACY_SPEC.loader.exec_module(legacy_install)


class InstallRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.assets_tmp = tempfile.TemporaryDirectory(prefix="director-recovery-assets-")
        cls.addClassCleanup(cls.assets_tmp.cleanup)
        cls.assets = Path(cls.assets_tmp.name)
        for version in ("v1.0.0", "v1.1.0"):
            build_release(REPO, version, cls.assets / version)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="director-recovery-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.home = self.base / "home with spaces"
        self.home.mkdir()
        self.managed = self.home / ".local/share/director"
        self.binary = self.home / ".local/bin/director"
        self.profile = self.home / ".zshrc"
        environment = patch.dict(os.environ, {
            "HOME": str(self.home), "DIRECTOR_HOME": str(self.managed),
            "SHELL": "/bin/zsh", "PATH": "/usr/bin:/bin",
        })
        environment.start()
        self.addCleanup(environment.stop)
        apps = patch.object(launch, "_run", side_effect=AssertionError("Unexpected app call"))
        apps.start()
        self.addCleanup(apps.stop)
        self.sources = {}
        for version in ("v1.0.0", "v1.1.0"):
            archive = self.assets / version / f"director-{version}.tar.gz"
            self.sources[version] = install.extract(archive, self.base / version)

    def activate(self, version="v1.0.0"):
        with install.locked(self.managed, create=True):
            previous = install.metadata(self.managed) if (self.managed / "install.json").exists() else None
            return install.activate(self.managed, self.sources[version], previous)

    def installed(self):
        data = self.activate()
        install.install_launcher(self.managed, data)
        return data

    def test_failed_first_validation_does_not_make_retry_unmanaged(self):
        version = self.sources["v1.0.0"] / "VERSION"
        version.write_text("invalid\n")
        with self.assertRaises(ValueError):
            self.activate()
        version.write_text("v1.0.0\n")
        data = self.activate()
        self.assertEqual(data["version"], "v1.0.0")
        self.assertTrue((self.managed / "current/ROLE.md").is_file())

    def test_first_install_recovers_after_release_was_moved_but_command_failed(self):
        replace = os.replace
        release = self.managed / "releases/v1.0.0"

        def interrupt_after_move(source, destination):
            replace(source, destination)
            if Path(destination) == release:
                raise OSError("interrupted immediately after release move")

        with patch.object(install.os, "replace", side_effect=interrupt_after_move):
            with self.assertRaisesRegex(OSError, "after release move"):
                self.activate()
        self.assertTrue(release.is_dir())
        self.activate()
        self.assertEqual((self.managed / "current").readlink(), Path("releases/v1.0.0"))
        self.assertFalse(list((self.managed / "releases").glob(".staging-*")))

    def test_failed_update_copy_keeps_previous_release_and_history_then_retries(self):
        self.installed()
        history = self.managed / "private/log.jsonl"
        history.write_text('{"run": 12, "reason": "keep my history", "decision": "leave", "picked": "bb:thread-a", "ts": "2026-09-01T10:00:00+00:00"}\n')
        contents = history.read_bytes()
        copytree = install.shutil.copytree

        def interrupted_copy(source, destination, *args, **kwargs):
            result = copytree(source, destination, *args, **kwargs)
            if Path(destination).name.startswith(".staging-"):
                raise OSError("disk full while staging update")
            return result

        with patch.object(install.shutil, "copytree", side_effect=interrupted_copy):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.activate("v1.1.0")
        self.assertEqual((self.managed / "current").readlink(), Path("releases/v1.0.0"))
        self.assertTrue(os.access(self.binary, os.X_OK))
        self.assertEqual(history.read_bytes(), contents)
        self.activate("v1.1.0")
        self.assertEqual((self.managed / "current").readlink(), Path("releases/v1.1.0"))
        self.assertEqual(history.read_bytes(), contents)

    def test_upgrade_migrates_legacy_release_and_keeps_its_data_link_usable(self):
        data = self.installed()
        release = self.managed / "releases/v1.0.0"
        # Model an installed release from before the profile/state layout.
        for name in ("profile", "state"):
            (release / name).unlink()
        (release / "director/storage.py").unlink()
        data["digests"]["v1.0.0"] = install.tree_digest(release)
        install.save(self.managed / "install.json", data)
        legacy = self.managed / "private"
        (legacy / "judgment").mkdir()
        (legacy / "judgment/qa.md").write_text("Q01: Keep this teaching.\n")
        (legacy / "log.jsonl").write_text('{"run": 12, "decision": "leave", "picked": "bb:thread-a", "ts": "2026-09-01T10:00:00+00:00"}\n')
        self.activate("v1.1.0")
        self.assertEqual((self.managed / "profile/qa.md").read_text(), "Q01: Keep this teaching.\n")
        with (release / "private/log.jsonl").open("a") as stream:
            stream.write('{"run": 13, "decision": "leave", "picked": "bb:thread-a", "ts": "2026-09-01T10:00:00+00:00"}\n')
        self.assertIn('"run": 13', (self.managed / "state/log.jsonl").read_text())
        lifecycle.uninstall(self.managed)
        self.assertTrue((self.managed / "profile/qa.md").is_file())

    def test_actual_legacy_installer_upgrades_new_commands_without_splitting_data(self):
        with legacy_install.locked(self.managed, create=True):
            data = legacy_install.activate(self.managed, self.sources["v1.0.0"])
            legacy_install.install_launcher(self.managed, data)
        release = self.managed / "releases/v1.0.0"
        self.assertFalse((release / "profile").exists())
        self.assertFalse((release / "state").exists())
        legacy = self.managed / "private"
        (legacy / "judgment").mkdir()
        (legacy / "judgment/qa.md").write_text("Q01: Preserve the original teaching.\n")
        record = {"run": 12, "ts": "2026-09-01T10:00:00+00:00", "picked": "bb:thread-a", "decision": "leave"}
        (legacy / "log.jsonl").write_text(json.dumps(record) + "\n")
        result = subprocess.run([sys.executable, str(release / "director/log.py"), "stats"],
                                cwd=self.base, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("runs 1", result.stdout)
        for name in ("profile", "state"):
            self.assertTrue((release / name).is_symlink())
            self.assertEqual((release / name).resolve(), (self.managed / name).resolve())
        self.assertEqual((self.managed / "profile/qa.md").read_text(), "Q01: Preserve the original teaching.\n")
        self.assertEqual(json.loads((self.managed / "state/log.jsonl").read_text()), record)
        lifecycle.uninstall(self.managed)
        self.assertTrue((self.managed / "profile/qa.md").is_file())

    def test_legacy_link_repair_preserves_changed_data_and_creates_no_partial_links(self):
        with legacy_install.locked(self.managed, create=True):
            legacy_install.activate(self.managed, self.sources["v1.0.0"])
        release = self.managed / "releases/v1.0.0"
        (release / "state").mkdir()
        local = release / "state/keep.md"
        local.write_text("This belongs to the user.\n")
        with self.assertRaisesRegex(ValueError, "state data link"):
            install.repair_managed_links(self.managed)
        self.assertEqual(local.read_text(), "This belongs to the user.\n")
        self.assertFalse((release / "profile").is_symlink())
        self.assertFalse((release / "profile").exists())

    @unittest.skipUnless(sys.platform == "darwin", "installer supports macOS")
    def test_actual_legacy_launcher_can_uninstall_through_new_lifecycle(self):
        with legacy_install.locked(self.managed, create=True):
            data = legacy_install.activate(self.managed, self.sources["v1.0.0"])
            legacy_install.install_launcher(self.managed, data)
        legacy = self.managed / "private/judgment"
        legacy.mkdir()
        (legacy / "qa.md").write_text("Keep my teaching after uninstall.\n")
        result = subprocess.run([str(self.binary), "uninstall"], cwd=self.base,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.managed / "profile/qa.md").read_text(), "Keep my teaching after uninstall.\n")
        self.assertFalse(self.binary.exists())
        self.assertFalse((self.managed / "releases").exists())

    def test_legacy_downgrade_preserves_current_release_and_teaching(self):
        self.installed()
        teaching = self.managed / "profile/qa.md"
        teaching.write_text("My teaching stays unchanged.\n")
        metadata = (self.managed / "install.json").read_bytes()
        (self.sources["v1.1.0"] / "director/storage.py").unlink()
        (self.sources["v1.1.0"] / "VERSION").write_text("v0.9.0\n")
        with self.assertRaisesRegex(ValueError, "older release cannot safely manage"):
            self.activate("v1.1.0")
        self.assertEqual(teaching.read_text(), "My teaching stays unchanged.\n")
        self.assertEqual((self.managed / "install.json").read_bytes(), metadata)
        self.assertEqual((self.managed / "current").readlink(), Path("releases/v1.0.0"))
        self.assertFalse((self.managed / "releases/v0.9.0").exists())

    def test_failed_launcher_replace_preserves_working_command_and_accepts_retry(self):
        data = self.installed()
        before = self.binary.read_bytes()
        replacement_python = self.base / "replacement-python"
        replacement_python.symlink_to(sys.executable)
        data["python"] = str(replacement_python)
        replace = os.replace

        def fail_launcher_replace(source, destination):
            if Path(destination) == self.binary:
                raise OSError("cannot replace launcher")
            return replace(source, destination)

        with patch.object(install.os, "replace", side_effect=fail_launcher_replace):
            with self.assertRaisesRegex(OSError, "replace launcher"):
                install.install_launcher(self.managed, data)
        self.assertEqual(self.binary.read_bytes(), before)
        self.assertTrue(os.access(self.binary, os.X_OK))
        recovered = install.metadata(self.managed)
        install.check_launcher(recovered)
        install.install_launcher(self.managed, recovered)
        self.assertIn(str(replacement_python), self.binary.read_text())
        self.assertTrue(os.access(self.binary, os.X_OK))

    def test_uninstall_retry_accepts_path_block_already_removed(self):
        data = self.installed()
        original = "# Existing user settings\nexport USER_SETTING=keep\n"
        self.profile.write_text(original)
        install.setup_path(self.managed, data)
        unlink = Path.unlink

        def interrupt_after_path_cleanup(path, *args, **kwargs):
            if path == self.binary:
                raise OSError("interrupted after PATH cleanup")
            return unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", interrupt_after_path_cleanup):
            with self.assertRaisesRegex(OSError, "PATH cleanup"):
                lifecycle.uninstall(self.managed)
        self.assertEqual(self.profile.read_text(), original)
        lifecycle.uninstall(self.managed)
        self.assertFalse(self.binary.exists())
        self.assertFalse((self.managed / "current").exists())
        self.assertEqual(self.profile.read_text(), original)
        self.assertTrue((self.managed / "private").is_dir())

    def test_plain_uninstall_preserves_data_replacing_release_private_symlink(self):
        self.installed()
        private = self.managed / "releases/v1.0.0/private"
        private.unlink()
        private.mkdir()
        personal = private / "important-rules.md"
        personal.write_text("My personal rules belong to me.\n")
        with self.assertRaises(ValueError):
            lifecycle.uninstall(self.managed)
        self.assertEqual(personal.read_text(), "My personal rules belong to me.\n")
        self.assertTrue(self.binary.exists())
        self.assertTrue((self.managed / "current/ROLE.md").exists())

    def test_uninstall_preserves_data_replacing_profile_or_state_link(self):
        self.installed()
        for name in ("profile", "state"):
            with self.subTest(name=name):
                path = self.managed / "releases/v1.0.0" / name
                path.unlink()
                path.mkdir()
                personal = path / "keep.md"
                personal.write_text("Local work must survive.\n")
                with self.assertRaises(ValueError):
                    lifecycle.uninstall(self.managed, purge=True)
                self.assertEqual(personal.read_text(), "Local work must survive.\n")
                self.assertTrue(self.binary.exists())
                personal.unlink()
                path.rmdir()
                path.symlink_to("../../" + name)

    def test_uninstall_recovers_from_partially_removed_release(self):
        self.installed()
        history = self.managed / "private/log.jsonl"
        history.write_text('{"run": 15, "reason": "keep this", "decision": "leave", "picked": "bb:thread-a", "ts": "2026-09-01T10:00:00+00:00"}\n')
        contents = history.read_bytes()
        release = self.managed / "releases/v1.0.0"
        rmtree = install.shutil.rmtree

        def interrupted_removal(path, *args, **kwargs):
            removing = Path(path)
            if removing.parent == self.managed / "releases" and (removing / "ROLE.md").exists():
                (removing / "ROLE.md").unlink()
                raise OSError("interrupted during release removal")
            return rmtree(path, *args, **kwargs)

        with patch.object(install.shutil, "rmtree", side_effect=interrupted_removal):
            with self.assertRaisesRegex(OSError, "release removal"):
                lifecycle.uninstall(self.managed)
        lifecycle.uninstall(self.managed)
        self.assertFalse(release.exists())
        self.assertEqual(history.read_bytes(), contents)

    def test_launcher_keeps_selected_interpreter_symlink_across_upgrade(self):
        executable = sys.executable
        first = self.base / "python-old"
        second = self.base / "python-new"
        selected = self.base / "selected-python"
        for target in (first, second):
            target.write_text("#!/bin/sh\nexec " + shlex.quote(executable) + ' "$@"\n')
            target.chmod(0o755)
        selected.symlink_to(first)
        with patch.object(install.sys, "executable", str(selected)):
            data = self.installed()
        self.assertEqual(data["python"], str(selected))
        selected.unlink()
        selected.symlink_to(second)
        first.unlink()
        result = subprocess.run([str(self.binary), "--version"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "v1.0.0")

    def test_invalid_python_release_does_not_replace_working_installation(self):
        self.installed()
        (self.sources["v1.1.0"] / "director/launch.py").write_text("def invalid(:\n")
        with self.assertRaisesRegex(ValueError, "invalid Python"):
            self.activate("v1.1.0")
        self.assertEqual((self.managed / "current").readlink(), Path("releases/v1.0.0"))
        self.assertTrue(self.binary.exists())

    def test_incomplete_public_profile_does_not_replace_working_installation(self):
        self.installed()
        teaching = self.managed / "profile/qa.md"
        teaching.write_text("My exact teaching stays here.\n")
        (self.sources["v1.1.0"] / "templates/profile/qa.md").unlink()
        metadata = (self.managed / "install.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "missing templates/profile/qa.md"):
            self.activate("v1.1.0")
        self.assertEqual(teaching.read_text(), "My exact teaching stays here.\n")
        self.assertEqual((self.managed / "install.json").read_bytes(), metadata)
        self.assertEqual((self.managed / "current").readlink(), Path("releases/v1.0.0"))

    def test_empty_public_template_does_not_activate_or_migrate_personal_data(self):
        self.installed()
        teaching = self.managed / "private/judgment/qa.md"
        teaching.parent.mkdir()
        teaching.write_text("Keep this legacy teaching in place on failure.\n")
        before = {path.relative_to(self.managed): path.read_bytes()
                  for name in ("profile", "state", "private") for path in (self.managed / name).rglob("*")
                  if path.is_file()}
        metadata = (self.managed / "install.json").read_bytes()
        (self.sources["v1.1.0"] / "templates/profile/qa.md").write_bytes(b" \n")
        with self.assertRaisesRegex(ValueError, "template"):
            self.activate("v1.1.0")
        self.assertEqual((self.managed / "install.json").read_bytes(), metadata)
        self.assertEqual((self.managed / "current").readlink(), Path("releases/v1.0.0"))
        self.assertFalse(teaching.is_symlink())
        self.assertFalse((self.managed / "releases/v1.1.0").exists())
        self.assertEqual({path.relative_to(self.managed): path.read_bytes()
                          for name in ("profile", "state", "private") for path in (self.managed / name).rglob("*")
                          if path.is_file()}, before)

    def test_template_symlink_and_unlisted_file_are_rejected_before_activation(self):
        self.installed()
        source = self.sources["v1.1.0"]
        teaching = self.managed / "profile/qa.md"
        teaching.write_text("Private teaching.\n")
        template = source / "templates/profile/qa.md"
        contents = template.read_bytes()
        template.unlink()
        template.symlink_to(teaching)
        with self.assertRaisesRegex(ValueError, "missing templates/profile/qa.md"):
            self.activate("v1.1.0")
        template.unlink()
        template.write_bytes(contents)
        (source / "templates/profile/notes.md").write_text("Unlisted content.\n")
        with self.assertRaisesRegex(ValueError, "Unexpected public template path"):
            self.activate("v1.1.0")
        self.assertEqual((self.managed / "current").readlink(), Path("releases/v1.0.0"))
        self.assertEqual(teaching.read_text(), "Private teaching.\n")

    def test_release_redirects_keep_https_and_confine_http_to_local_fixtures(self):
        redirects = install.ReleaseRedirects()
        for source, destination in (
            ("https://github.com/release", "http://downloads.example/release"),
            ("https://github.com/release", "http://127.0.0.1/release"),
            ("http://127.0.0.1/release", "http://downloads.example/release"),
            ("https://github.com/release", "file:///tmp/release"),
        ):
            with self.subTest(source=source, destination=destination):
                with self.assertRaises(ValueError):
                    redirects.redirect_request(urllib.request.Request(source), None, 302, "Found", {}, destination)
        for source, destination in (
            ("https://github.com/release", "https://release-assets.githubusercontent.com/release"),
            ("http://127.0.0.1/release", "http://localhost/release"),
        ):
            with self.subTest(source=source, destination=destination):
                request = redirects.redirect_request(urllib.request.Request(source), None, 302, "Found", {}, destination)
                self.assertEqual(request.full_url, destination)

    @unittest.skipUnless(sys.platform == "darwin", "installer supports macOS")
    def test_downloaded_cli_installs_with_standard_python_bytecode_behavior(self):
        source = self.sources["v1.0.0"]
        script = source / "director/cli.py"
        # Apple's system Python caches elsewhere. Force the standard behavior used
        # by Homebrew Python so imported installer modules would cache in the release.
        runner = (
            "import runpy, sys; "
            "sys.pycache_prefix = None; sys.dont_write_bytecode = False; "
            "sys.path.insert(0, sys.argv[1]); sys.argv = sys.argv[2:]; "
            "runpy.run_path(sys.argv[0], run_name='__main__')"
        )
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("PYTHON", "BB_", "CMUX_"))}
        result = subprocess.run([
            sys.executable, "-B", "-c", runner, str(script.parent), str(script),
            "--home", str(self.managed), "install", "--source", str(source), "--no-setup",
        ], cwd=self.base, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(list(source.rglob("__pycache__")))
        self.assertTrue(os.access(self.binary, os.X_OK))


if __name__ == "__main__":
    unittest.main()
