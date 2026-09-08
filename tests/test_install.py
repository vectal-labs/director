"""Exercise the downloaded installer and installed CLI with isolated macOS homes."""
import functools
import hashlib
import http.server
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import threading
import unittest

from director.install import tree_digest
from scripts.build_release import build_release, TEMPLATE_FILES


REPO = Path(__file__).resolve().parents[1]

# Exact validator from 087d61d814982a4b74a44bb310cebf84af16dd0a. Keep this fixture
# independent of the new validator so an already-installed updater is exercised.
PRE_TEMPLATE_VALIDATOR = '''def validate_source(source):
    version = (source / "VERSION").read_text().strip()
    if not VERSION.fullmatch(version):
        raise ValueError("Invalid release VERSION.")
    for name in ("ROLE.md", "director/cli.py", "director/install.py", "director/lifecycle.py", "director/launch.py",
                 ".agents/skills/director-bb/SKILL.md", ".agents/skills/director-cmux/SKILL.md"):
        path = source / name
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Release is missing {name}.")
    for path in source.rglob("*"):
        relative = path.relative_to(source).as_posix()
        if path.is_symlink() and LINKS.get(relative) != os.readlink(path):
            raise ValueError(f"Unexpected release symlink: {relative}")
        if any(p in {*DATA_DIRS, ".git", "__pycache__"} or p.startswith(".env") for p in path.relative_to(source).parts):
            raise ValueError(f"Private or generated file in release: {relative}")
        if path.suffix == ".py" and path.is_file():
            try:
                compile(path.read_bytes(), relative, "exec")
            except (SyntaxError, ValueError) as error:
                raise ValueError(f"Release contains invalid Python: {relative}") from error
    return version


'''


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


@unittest.skipUnless(sys.platform == "darwin", "installer supports macOS")
class InstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.assets_tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.assets_tmp.cleanup)
        cls.assets = Path(cls.assets_tmp.name)
        for version in ("v1.0.0", "v1.1.0"):
            build_release(REPO, version, cls.assets / version)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "home with spaces"
        self.home.mkdir()
        self.managed = self.home / ".local/share/director"
        self.binary = self.home / ".local/bin/director"
        self.tools = self.root / "tools"
        self.tools.mkdir()
        (self.tools / "python3").symlink_to(sys.executable)
        self.calls = self.root / "app-calls.jsonl"
        for app in ("bb", "cmux", "claude", "codex"):
            fake = self.tools / app
            fake.write_text("#!/bin/sh\nprintf '%s\\n' \"$0 $*\" >> \"$FAKE_APP_CALLS\"\nexit 99\n")
            fake.chmod(0o755)
        self.web = self.root / "web"
        shutil.copytree(self.assets, self.web)
        handler = functools.partial(QuietHandler, directory=str(self.web))
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("BB_", "CMUX_", "DIRECTOR_", "PYTHON"))}
        self.env.update(
            HOME=str(self.home), DIRECTOR_HOME=str(self.managed), SHELL="/bin/zsh",
            PATH=f"{self.tools}:/usr/bin:/bin:/usr/sbin:/sbin",
            FAKE_APP_CALLS=str(self.calls), PYTHONDONTWRITEBYTECODE="1",
            PYTHONPYCACHEPREFIX=str(self.root / "pycache"),
            DIRECTOR_RELEASE_BASE_URL=self.url("v1.0.0"),
        )

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def url(self, version):
        return f"http://127.0.0.1:{self.server.server_port}/{version}"

    def run_command(self, command, *, version=None, input=""):
        env = dict(self.env)
        if version:
            env["DIRECTOR_RELEASE_BASE_URL"] = self.url(version)
        return subprocess.run(command, input=input, cwd=self.root, env=env,
                              capture_output=True, text=True, timeout=40)

    def bootstrap(self, *args, version=None):
        return self.run_command(["/bin/sh", str(REPO / "install.sh"), *args], version=version)

    def install(self, *args):
        result = self.bootstrap("--no-setup", *args)
        self.assert_success(result)
        return result

    def cli(self, *args, version=None):
        return self.run_command([str(self.binary), *args], version=version)

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def assert_failure(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def personal_data(self):
        files = {
            "profile/what.md": "My exact personal rules.\n",
            "profile/qa.md": "Q01: My exact answer.\n",
            "state/log.jsonl": '{"run": 42, "decision": "leave", "picked": "bb:thread-a", "ts": "2026-09-01T10:00:00+00:00"}\n',
            "state/scans/old.json": '{"saved": "history"}\n',
        }
        for relative, contents in files.items():
            path = self.managed / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents)
        return files

    def assert_personal_data(self, files):
        for relative, contents in files.items():
            self.assertEqual((self.managed / relative).read_text(), contents)

    def replace_archive(self, version, member=None, *, replacements=None, remove_prefixes=()):
        archive = self.web / version / f"director-{version}.tar.gz"
        with tarfile.open(archive, "r:gz") as source:
            entries = [(item, source.extractfile(item).read() if item.isfile() else None)
                       for item in source.getmembers()]
        with tarfile.open(archive, "w:gz") as destination:
            for item, content in entries:
                if any(item.name == prefix or item.name.startswith(prefix + "/")
                       for prefix in remove_prefixes):
                    continue
                if item.name in (replacements or {}):
                    content = replacements[item.name]
                    item.size = len(content)
                destination.addfile(item, io.BytesIO(content) if content is not None else None)
            if member is not None:
                destination.addfile(member, io.BytesIO(b"unexpected") if member.isfile() else None)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        (archive.parent / "SHA256SUMS").write_text(f"{digest}  {archive.name}\n")

    def enable_fake_bb(self):
        self.env["FAKE_BB_STATE"] = str(self.root / "fake-bb-state.json")
        program = textwrap.dedent('''\
            import json, os, pathlib, sys
            args = sys.argv[1:]
            with open(os.environ["FAKE_APP_CALLS"], "a") as stream:
                stream.write(json.dumps(args) + "\\n")
            file = pathlib.Path(os.environ["FAKE_BB_STATE"])
            state = json.loads(file.read_text()) if file.exists() else {}
            def flag(name):
                return args[args.index(name) + 1]
            if args[:2] == ["project", "list"]:
                result = [state["project"]] if "project" in state else []
            elif args[:2] == ["project", "create"]:
                result = {"id": "project-director", "name": flag("--name"),
                          "sources": [{"path": flag("--root"), "hostId": "host-local"}]}
                state["project"] = result
            elif args[:2] == ["provider", "list"]:
                result = [{"id": "fake-provider", "available": True,
                           "capabilities": {"permissionModes": ["accept-edits"]}}]
            elif args[:2] == ["provider", "models"]:
                result = [{"id": "fake-model", "isDefault": True}]
            elif args[:2] == ["thread", "spawn"]:
                result = {"id": "thread-director", "projectId": flag("--project"),
                          "title": flag("--title"), "status": "active"}
                state["thread"] = result
                state["environment"] = {"path": flag("--environment"), "hostId": "host-local"}
            elif args[:2] == ["thread", "list"]:
                result = [state["thread"]] if "thread" in state and "--archived" not in args else []
            elif args[:2] == ["thread", "show"]:
                if "thread" not in state:
                    print("Error: HTTP 404: Thread not found", file=sys.stderr)
                    sys.exit(1)
                result = {"thread": state["thread"], "environment": state["environment"]}
            elif args[:2] == ["thread", "stop"]:
                state["thread"]["status"] = "idle"
                result = {}
            elif args[:2] == ["thread", "delete"]:
                del state["thread"]
                result = {}
            elif args[:2] == ["project", "delete"]:
                del state["project"]
                result = {}
            else:
                sys.exit(99)
            file.write_text(json.dumps(state))
            print(json.dumps(result))
        ''')
        (self.tools / "bb").write_text(f"#!{sys.executable}\n" + program)

    def test_fresh_install_exposes_version_skills_and_shared_personal_directories(self):
        self.install()
        result = self.cli("--version")
        self.assert_success(result)
        self.assertEqual(result.stdout.strip(), "v1.0.0")
        self.assertEqual((self.managed / "current").readlink(), Path("releases/v1.0.0"))
        self.assertTrue((self.managed / "current/ROLE.md").is_file())
        self.assertEqual((self.managed / "docs/memory.md").read_text(),
                         (REPO / "docs/memory.md").read_text())
        self.assertEqual((self.managed / "templates").readlink(), Path("current/templates"))
        for relative in TEMPLATE_FILES:
            self.assertEqual((self.managed / relative).read_bytes(), (REPO / relative).read_bytes())
        self.assertTrue((self.managed / "current/.claude/skills/director-bb/SKILL.md").is_file())
        for name in ("profile", "state", "private"):
            self.assertEqual((self.managed / "current" / name).resolve(), (self.managed / name).resolve())
        self.assertTrue(os.access(self.binary, os.X_OK))
        self.assertFalse(self.calls.exists(), "file-only installation must not call app CLIs")

    def test_installer_can_be_piped_into_sh(self):
        result = self.run_command(["/bin/sh", "-s", "--", "--no-setup"],
                                  input=(REPO / "install.sh").read_text())
        self.assert_success(result)
        self.assert_success(self.cli("--version"))

    def test_packaged_setup_copies_public_starters_and_keeps_existing_profile(self):
        self.enable_fake_bb()
        result = self.bootstrap("--app", "bb", "--provider", "fake-provider",
                                "--model", "fake-model", "--yes", "--no-start")
        self.assert_success(result)
        for relative in TEMPLATE_FILES:
            active = self.managed / "profile" / Path(relative).name
            self.assertEqual(active.read_bytes(), (REPO / relative).read_bytes())
        files = self.personal_data()
        before = {path.name: path.read_bytes() for path in (self.managed / "profile").iterdir()}
        self.assert_success(self.cli("setup", "--yes", "--no-start"))
        self.assertEqual({path.name: path.read_bytes() for path in (self.managed / "profile").iterdir()}, before)
        self.assert_personal_data(files)

    def test_managed_setup_rejects_invalid_templates_before_app_calls_or_state_writes(self):
        self.install()
        self.personal_data()
        template = self.managed / "current/templates/profile/qa.md"
        template.write_bytes(b"\xffinvalid UTF-8")
        # Model a malformed release already accepted by the previous installer,
        # so ownership validation does not mask the missing template preflight.
        record = self.managed / "install.json"
        data = json.loads(record.read_text())
        data["digests"]["v1.0.0"] = tree_digest(self.managed / "current")
        record.write_text(json.dumps(data) + "\n")
        self.enable_fake_bb()
        before = {path.relative_to(self.managed): path.read_bytes()
                  for name in ("profile", "state") for path in (self.managed / name).rglob("*")
                  if path.is_file()}
        result = self.cli("setup", "--app", "bb", "--provider", "fake-provider",
                          "--model", "fake-model", "--yes", "--no-start")
        self.assert_failure(result)
        self.assertIn("template", result.stderr.lower())
        self.assertFalse(self.calls.exists(), "invalid public templates must be rejected before app calls")
        self.assertFalse(Path(self.env["FAKE_BB_STATE"]).exists())
        self.assertFalse((self.managed / "state/config.json").exists())
        self.assertEqual({path.relative_to(self.managed): path.read_bytes()
                          for name in ("profile", "state") for path in (self.managed / name).rglob("*")
                          if path.is_file()}, before)

    def test_invalid_template_archive_preserves_current_release_and_personal_bytes(self):
        self.install()
        self.personal_data()
        before = {path.relative_to(self.managed): path.read_bytes()
                  for name in ("profile", "state") for path in (self.managed / name).rglob("*")
                  if path.is_file()}
        metadata = (self.managed / "install.json").read_bytes()
        self.replace_archive("v1.1.0", replacements={
            "director/templates/profile/qa.md": b"\xffinvalid UTF-8",
        })
        result = self.cli("update", version="v1.1.0")
        self.assert_failure(result)
        self.assertIn("template", result.stderr.lower())
        self.assertEqual((self.managed / "install.json").read_bytes(), metadata)
        self.assertEqual(self.cli("--version").stdout.strip(), "v1.0.0")
        self.assertFalse((self.managed / "releases/v1.1.0").exists())
        self.assertEqual({path.relative_to(self.managed): path.read_bytes()
                          for name in ("profile", "state") for path in (self.managed / name).rglob("*")
                          if path.is_file()}, before)

    def test_changed_public_starters_on_update_only_fill_missing_personal_files(self):
        self.enable_fake_bb()
        self.install()
        self.assert_success(self.cli("setup", "--app", "bb", "--provider", "fake-provider",
                                     "--model", "fake-model", "--yes", "--no-start"))
        files = self.personal_data()
        profile = self.managed / "profile"
        (profile / "qa.md").write_bytes("My exact answer: café.\r\n".encode("utf-8"))
        before = {path.name: path.read_bytes() for path in profile.iterdir()}
        updated = {"director/" + relative: ("# New public default\n\n" + relative + "\n").encode()
                   for relative in TEMPLATE_FILES}
        self.replace_archive("v1.1.0", replacements=updated)
        self.assert_success(self.cli("update", version="v1.1.0"))
        self.assert_success(self.cli("setup", "--yes", "--no-start"))
        self.assertEqual({path.name: path.read_bytes() for path in profile.iterdir()}, before)
        self.assertEqual((self.managed / "state/log.jsonl").read_text(), files["state/log.jsonl"])
        self.assertEqual((self.managed / "state/scans/old.json").read_text(), files["state/scans/old.json"])
        (profile / "limits.md").unlink()
        self.assert_success(self.cli("setup", "--yes", "--no-start"))
        self.assertEqual((profile / "limits.md").read_bytes(), updated["director/templates/profile/limits.md"])
        for name, contents in before.items():
            if name != "limits.md":
                self.assertEqual((profile / name).read_bytes(), contents)

    def test_bootstrap_updates_pre_template_validator_and_preserves_personal_bytes(self):
        installer = (REPO / "director/install.py").read_text()
        start, end = installer.index("def validate_source("), installer.index("def activate(")
        installer = installer[:start] + PRE_TEMPLATE_VALIDATOR + installer[end:]
        installer = installer.replace('"docs", "templates", ".agents"', '"docs", ".agents"')
        self.replace_archive("v1.0.0", replacements={"director/director/install.py": installer.encode()},
                             remove_prefixes=("director/templates",))
        self.install()
        self.personal_data()
        before = {path.relative_to(self.managed): path.read_bytes()
                  for name in ("profile", "state") for path in (self.managed / name).rglob("*")
                  if path.is_file()}
        metadata = (self.managed / "install.json").read_bytes()
        result = self.cli("update", version="v1.1.0")
        self.assert_failure(result)
        self.assertIn("Private or generated file in release: templates/profile", result.stderr)
        self.assertEqual((self.managed / "install.json").read_bytes(), metadata)
        self.assertEqual(self.cli("--version").stdout.strip(), "v1.0.0")
        self.assert_success(self.bootstrap("--no-setup", version="v1.1.0"))
        self.assertEqual(self.cli("--version").stdout.strip(), "v1.1.0")
        self.assertEqual({path.relative_to(self.managed): path.read_bytes()
                          for name in ("profile", "state") for path in (self.managed / name).rglob("*")
                          if path.is_file()}, before)
        for relative in TEMPLATE_FILES:
            self.assertEqual((self.managed / relative).read_bytes(), (REPO / relative).read_bytes())

    def test_install_rerun_preserves_rules_history_and_single_path_block(self):
        self.install()
        files = self.personal_data()
        profile = self.home / ".zshrc"
        before = profile.read_text()
        self.install()
        self.assert_personal_data(files)
        self.assertEqual(profile.read_text(), before)
        self.assertEqual(json.loads((self.managed / "install.json").read_text())["versions"], ["v1.0.0"])

    def test_successful_update_preserves_data_and_switches_current_release(self):
        self.install()
        files = self.personal_data()
        result = self.cli("update", version="v1.1.0")
        self.assert_success(result)
        self.assertEqual(self.cli("--version").stdout.strip(), "v1.1.0")
        self.assertEqual((self.managed / "current").readlink(), Path("releases/v1.1.0"))
        self.assertTrue((self.managed / "releases/v1.0.0/ROLE.md").exists())
        self.assert_personal_data(files)
        for version in ("v1.0.0", "v1.1.0"):
            for name in ("profile", "state"):
                self.assertEqual((self.managed / "releases" / version / name).resolve(),
                                 (self.managed / name).resolve())

    def test_purge_keeps_unrelated_private_notes(self):
        self.install()
        self.personal_data()
        note = self.managed / "private/brief.md"
        note.write_text("Unrelated notes must survive even a purge.\n")
        self.assert_success(self.cli("uninstall", "--purge"))
        self.assertEqual(note.read_text(), "Unrelated notes must survive even a purge.\n")
        self.assertFalse((self.managed / "profile").exists())
        self.assertFalse((self.managed / "state").exists())
        self.assertFalse(self.binary.exists())

    def test_setup_starts_fake_bb_once_and_purge_removes_its_owned_session(self):
        self.install()
        self.enable_fake_bb()
        result = self.cli("setup", "--app", "bb", "--provider", "fake-provider",
                          "--model", "fake-model", "--yes")
        self.assert_success(result)
        config = json.loads((self.managed / "state/config.json").read_text())
        self.assertEqual((config["app"], config["provider"], config["model"]),
                         ("bb", "fake-provider", "fake-model"))
        self.assertTrue((self.managed / "profile/limits.md").exists())
        self.assert_success(self.cli("start"))
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        starts = [args for args in calls if args[:2] == ["thread", "spawn"]]
        self.assertEqual(len(starts), 1)
        self.assertIn("Read ROLE.md", starts[0][starts[0].index("--prompt") + 1])
        self.assert_success(self.cli("uninstall", "--purge"))
        state = json.loads(Path(self.env["FAKE_BB_STATE"]).read_text())
        self.assertNotIn("thread", state)
        self.assertNotIn("project", state)
        self.assertFalse(self.managed.exists())

    def test_update_checksum_failure_leaves_working_installation_unchanged(self):
        self.install()
        files = self.personal_data()
        metadata = (self.managed / "install.json").read_bytes()
        launcher = self.binary.read_bytes()
        archive = self.web / "v1.1.0/director-v1.1.0.tar.gz"
        archive.write_bytes(archive.read_bytes() + b"corrupted")
        result = self.cli("update", version="v1.1.0")
        self.assert_failure(result)
        self.assertIn("checksum", result.stderr.lower())
        self.assertEqual((self.managed / "install.json").read_bytes(), metadata)
        self.assertEqual(self.binary.read_bytes(), launcher)
        self.assertEqual(self.cli("--version").stdout.strip(), "v1.0.0")
        self.assertFalse((self.managed / "releases/v1.1.0").exists())
        self.assert_personal_data(files)

    def test_fresh_checksum_failure_does_not_install_files(self):
        (self.web / "v1.0.0/SHA256SUMS").write_text("0" * 64 + "  director-v1.0.0.tar.gz\n")
        result = self.bootstrap("--no-setup")
        self.assert_failure(result)
        self.assertIn("checksum", result.stderr.lower())
        self.assertFalse(self.binary.exists())
        self.assertFalse((self.managed / "current").exists())

    def test_uninstall_preserves_private_data_and_restores_existing_profile(self):
        profile = self.home / ".zshrc"
        original = "# Existing shell settings\nexport MY_SETTING='keep this'"
        profile.write_text(original)
        self.install()
        files = self.personal_data()
        added_later = "\n# User setting added after installation\n"
        with profile.open("a") as stream:
            stream.write(added_later)
        result = self.cli("uninstall")
        self.assert_success(result)
        self.assertFalse(self.binary.exists())
        self.assertFalse((self.managed / "current").exists())
        self.assertFalse((self.managed / "releases").exists())
        self.assertEqual(profile.read_text(), original + added_later)
        self.assert_personal_data(files)
        self.assertFalse(self.calls.exists(), "an install with no sessions must not contact apps")

    def test_purge_after_reinstall_removes_all_owned_local_files(self):
        self.install()
        files = self.personal_data()
        self.assert_success(self.cli("uninstall"))
        self.install()
        self.assert_personal_data(files)
        self.assert_success(self.cli("uninstall", "--purge"))
        self.assertFalse(self.managed.exists())
        self.assertFalse(self.binary.exists())
        self.assertFalse((self.home / ".zshrc").exists())

    def test_bootstrap_purges_data_kept_by_previous_uninstall(self):
        self.install()
        self.personal_data()
        self.assert_success(self.cli("uninstall"))
        result = self.bootstrap("--uninstall", "--purge")
        self.assert_success(result)
        self.assertFalse(self.managed.exists())
        self.assertFalse(self.binary.exists())

    def test_command_collision_is_preserved_and_retry_after_removal_works(self):
        self.binary.parent.mkdir(parents=True)
        self.binary.write_text("another application's command\n")
        result = self.bootstrap("--no-setup")
        self.assert_failure(result)
        self.assertEqual(self.binary.read_text(), "another application's command\n")
        self.assertFalse((self.managed / "current").exists())
        self.assertFalse((self.home / ".zshrc").exists())
        self.binary.unlink()
        self.install()

    def test_unmanaged_install_directory_is_preserved(self):
        self.managed.mkdir(parents=True)
        personal = self.managed / "my-file.txt"
        personal.write_text("unrelated work\n")
        result = self.bootstrap("--no-setup")
        self.assert_failure(result)
        self.assertEqual(personal.read_text(), "unrelated work\n")
        self.assertEqual({p.name for p in self.managed.iterdir()}, {"my-file.txt"})
        self.assertFalse(self.binary.exists())

    def test_archive_traversal_absolute_paths_and_outbound_links_are_rejected(self):
        escape = self.root / "escaped-file"
        bad_members = []
        for name in ("director/../escaped-file", str(escape)):
            item = tarfile.TarInfo(name)
            item.size = len(b"unexpected")
            bad_members.append(item)
        item = tarfile.TarInfo("director/outbound-link")
        item.type = tarfile.SYMTYPE
        item.linkname = str(self.root)
        bad_members.append(item)
        item = tarfile.TarInfo("director/hard-link")
        item.type = tarfile.LNKTYPE
        item.linkname = str(escape)
        bad_members.append(item)
        for item in bad_members:
            with self.subTest(name=item.name):
                shutil.copytree(self.assets / "v1.0.0", self.web / "v1.0.0", dirs_exist_ok=True)
                self.replace_archive("v1.0.0", item)
                result = self.bootstrap("--no-setup")
                self.assert_failure(result)
                self.assertFalse(self.binary.exists())
                self.assertFalse((self.managed / "current").exists())
                self.assertFalse(escape.exists())

    def test_update_with_unsafe_archive_keeps_previous_release(self):
        self.install()
        item = tarfile.TarInfo("director/../../escape")
        item.size = len(b"unexpected")
        self.replace_archive("v1.1.0", item)
        result = self.cli("update", version="v1.1.0")
        self.assert_failure(result)
        self.assertEqual(self.cli("--version").stdout.strip(), "v1.0.0")
        self.assertEqual((self.managed / "current").readlink(), Path("releases/v1.0.0"))

    def test_unlisted_template_files_and_personal_paths_are_rejected(self):
        self.install()
        files = self.personal_data()
        for name in ("templates/profile/settings.json", "templates/profile/.env",
                     "templates/profile/private/qa.md", "templates/other/qa.md",
                     "profile/qa.md", "state/log.jsonl", "private/notes.md"):
            with self.subTest(name=name):
                shutil.copytree(self.assets / "v1.1.0", self.web / "v1.1.0", dirs_exist_ok=True)
                member = tarfile.TarInfo("director/" + name)
                member.size = len(b"unexpected")
                self.replace_archive("v1.1.0", member)
                self.assert_failure(self.cli("update", version="v1.1.0"))
                self.assert_failure(self.bootstrap("--no-setup", version="v1.1.0"))
                self.assertEqual(self.cli("--version").stdout.strip(), "v1.0.0")
                self.assert_personal_data(files)

    def test_edited_launcher_blocks_uninstall_without_removing_data(self):
        self.install()
        files = self.personal_data()
        command = self.managed / "current/director/cli.py"
        self.binary.write_text("another application's replacement command\n")
        result = self.run_command([sys.executable, str(command), "uninstall", "--purge"])
        self.assert_failure(result)
        self.assertEqual(self.binary.read_text(), "another application's replacement command\n")
        self.assertTrue((self.managed / "current/ROLE.md").is_file())
        self.assert_personal_data(files)

    def test_modified_shell_profile_blocks_uninstall_before_any_removal(self):
        self.install()
        files = self.personal_data()
        profile = self.home / ".zshrc"
        profile.write_text(profile.read_text().replace('export PATH=', 'export CUSTOM_PATH='))
        result = self.cli("uninstall", "--purge")
        self.assert_failure(result)
        self.assertTrue(self.binary.exists())
        self.assertTrue((self.managed / "current/ROLE.md").is_file())
        self.assert_personal_data(files)


if __name__ == "__main__":
    unittest.main()
