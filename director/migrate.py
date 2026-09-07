#!/usr/bin/env python3
"""Move known Director data to profile/ and state/, preserving legacy aliases."""
import argparse
import ast
from contextlib import ExitStack
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

try:
    from . import lessons, storage
except ImportError:
    import lessons
    import storage


FILES = {
    **{f"judgment/{name}.md": f"profile/{name}.md" for name in ("what", "how", "limits", "qa")},
    "priorities.json": "profile/priorities.json",
    "log.jsonl": "state/log.jsonl",
    "config.json": "state/config.json",
    "launches.json": "state/launches.json",
}
DIRECTORIES = {"scans": "state/scans"}


def layout_root(root):
    root = Path(root).resolve()
    if root.parent.name == "releases":
        owner = root.parent.parent
        if (root / "private").is_symlink() and os.readlink(root / "private") == "../../private":
            for name in ("profile", "state"):
                path = root / name
                if path.is_symlink() and os.readlink(path) != f"../../{name}":
                    raise ValueError(f"Changed managed data link: {path}")
                if path.exists() and not path.is_symlink():
                    raise ValueError(f"Unexpected data inside release: {path}")
            return owner
    return root


def _exists(path):
    return path.exists() or path.is_symlink()


def _alias(source, target):
    return source.is_symlink() and source.resolve() == target.resolve()


def _plain_directory(path):
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise ValueError(f"Expected a local directory: {path}")


def _files(path):
    """Reject links instead of copying data from outside the known legacy folder."""
    _plain_directory(path)
    result = {}
    for item in path.rglob("*"):
        if item.is_symlink() or not (item.is_file() or item.is_dir()):
            raise ValueError(f"Unsupported legacy data entry: {item}")
        if item.is_file():
            result[item.relative_to(path)] = item
    return result


def _check_pair(source, target, directory=False):
    if _alias(source, target):
        return False
    if not _exists(source):
        return False
    if source.is_symlink() or target.is_symlink():
        raise ValueError(f"Unexpected data link: {source} or {target}")
    if directory:
        source_files = _files(source)
        if target.exists():
            target_files = _files(target)
            if set(source_files) != set(target_files) or any(
                p.read_bytes() != target_files[name].read_bytes() for name, p in source_files.items()
            ):
                raise ValueError(f"Conflicting Director data: {source} and {target}")
    else:
        if not source.is_file() or (target.exists() and not target.is_file()):
            raise ValueError(f"Expected a data file: {source} or {target}")
        if target.exists() and source.read_bytes() != target.read_bytes():
            raise ValueError(f"Conflicting Director data: {source} and {target}")
        if source.name == "log.jsonl" and target.exists() and not source.samefile(target):
            raise ValueError(f"Conflicting log files: {source} and {target} have separate writer handles; preserve both and resolve them before migration")
    return True


def _settings_from_text(text):
    tree = ast.parse(text)
    values = {}
    names = {"RECHECK_SECONDS": "recheck_seconds", "SPOT_CHECK_CHANCE": "spot_check_chance"}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in names:
                    try:
                        value = ast.literal_eval(node.value)
                    except (ValueError, TypeError):
                        continue
                    if type(value) in (int, float):
                        values[names[target.id]] = value
        if isinstance(node, ast.FunctionDef) and node.name == "effective_decision":
            for child in ast.walk(node):
                if isinstance(child, ast.Dict):
                    try:
                        value = ast.literal_eval(child)
                    except (ValueError, TypeError):
                        continue
                    if value and all(isinstance(k, str) and isinstance(v, str)
                                     and v in {"unblock", "leave", "wait_for_david", "deny"}
                                     for k, v in value.items()):
                        values["legacy_override_decisions"] = value
    return values


def legacy_settings(root):
    """Read old policy literals, including the previous code after an upgrade."""
    root = Path(root)
    current = root / "director/memory.py"
    if current.exists():
        values = _settings_from_text(current.read_text(encoding="utf-8"))
        if values:
            return values
    # Old updaters switch current before the new migration runs. Keep their old
    # code as the source of legacy settings, never a shared list of personal rules.
    metadata = root / "install.json"
    if metadata.exists():
        data = json.loads(metadata.read_text())
        for version in reversed(data.get("versions", [])):
            if not isinstance(version, str) or not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", version):
                raise ValueError("Invalid installed release record")
            source = root / "releases" / version / "director/memory.py"
            if source.exists():
                values = _settings_from_text(source.read_text(encoding="utf-8"))
                if values:
                    return values
    # Source checkouts may already have pulled the new code. Read local Git
    # history only; no fetch, shell evaluation, or old Python execution.
    if (root / ".git").exists():
        for revision in ("HEAD", "HEAD^"):
            try:
                result = subprocess.run(["git", "-C", str(root), "show", f"{revision}:director/memory.py"],
                                        capture_output=True, text=True, timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                break
            if result.returncode == 0:
                values = _settings_from_text(result.stdout)
                if values:
                    return values
    return {}


def _link(source, target):
    temporary = source.with_name(source.name + ".director-link")
    if _exists(temporary):
        if not _alias(temporary, target):
            raise ValueError(f"Unexpected migration file: {temporary}")
        temporary.unlink()
    temporary.symlink_to(os.path.relpath(target, source.parent), target_is_directory=target.is_dir())
    os.replace(temporary, source)


def _backup(source, backup, directory):
    backup.parent.mkdir(parents=True, exist_ok=True)
    if backup.exists():
        # Retain the first snapshot even if an old writer appended after an interruption.
        if backup.is_symlink() or backup.is_dir() != directory:
            raise ValueError(f"Unexpected migration backup: {backup}")
    elif directory:
        staging = Path(tempfile.mkdtemp(prefix=".copy-", dir=backup.parent))
        try:
            shutil.copytree(source, staging / "data")
            os.replace(staging / "data", backup)
        finally:
            shutil.rmtree(staging)
    else:
        descriptor, temporary = tempfile.mkstemp(prefix=".copy-", dir=backup.parent)
        os.close(descriptor)
        try:
            shutil.copy2(source, temporary)
            os.replace(temporary, backup)
        finally:
            Path(temporary).unlink(missing_ok=True)


def migrate(root=storage.ROOT):
    """Idempotent migration. Unknown private files and historical bytes stay intact."""
    root = layout_root(root)
    if (root / "install.json").exists() and (root / "current").is_symlink():
        # An older updater can install this release with only the legacy data link.
        try:
            from . import install
        except ImportError:
            import install
        install.repair_managed_links(root)
    for name in ("private", "profile", "state"):
        _plain_directory(root / name)
    state = root / "state"
    state.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        lock_path = state / ".layout.lock"
        if lock_path.is_symlink():
            raise ValueError(f"Unexpected migration lock link: {lock_path}")
        lock = stack.enter_context(lock_path.open("a+"))
        fcntl.flock(lock, fcntl.LOCK_EX)
        memory_lock_path = state / ".memory.lock"
        if memory_lock_path.is_symlink():
            raise ValueError(f"Unexpected memory lock link: {memory_lock_path}")
        memory_lock = stack.enter_context(memory_lock_path.open("a+"))
        fcntl.flock(memory_lock, fcntl.LOCK_EX)
        legacy_log = root / "private/log.jsonl"
        locked_inodes = set()
        for path in (state / "log.jsonl", legacy_log):
            if path.exists():
                identity = (path.stat().st_dev, path.stat().st_ino)
                if identity not in locked_inodes:
                    log = stack.enter_context(path.open("r"))
                    fcntl.flock(log, fcntl.LOCK_EX)
                    locked_inodes.add(identity)
        backup = state / "migration-backup"
        _plain_directory(backup)
        manifest = backup / "manifest.json"
        if manifest.is_symlink():
            raise ValueError(f"Unexpected migration manifest link: {manifest}")
        planned = json.loads(manifest.read_text()) if manifest.exists() else []
        mappings = {**FILES, **DIRECTORIES}
        if not isinstance(planned, list) or any(name not in mappings for name in planned):
            raise ValueError(f"Invalid migration manifest: {manifest}")
        recovering = []
        for old in planned:
            source, target = root / "private" / old, root / mappings[old]
            if not _exists(source) and target.exists():
                if target.is_symlink():
                    raise ValueError(f"Unexpected data link: {target}")
                recovering.append((source, target))
        pending = []
        for mapping, directory in ((FILES, False), (DIRECTORIES, True)):
            for old, new in mapping.items():
                source, target = root / "private" / old, root / new
                if source.parent != root / "private":
                    _plain_directory(source.parent)
                if _check_pair(source, target, directory):
                    pending.append((source, target, directory))
        history = legacy_log if legacy_log.exists() else state / "log.jsonl"
        history_text = history.read_text() if history.exists() else ""
        # Verify teaching conflicts before moving any files; materialize journals below.
        lessons.reconcile(history_text, root, write=False)
        if not pending and not recovering:
            lessons.reconcile(history_text, root)
            return {"migrated": [], "backup": None}
        settings = legacy_settings(root)
        settings_path = root / "profile/settings.json"
        if settings_path.is_symlink():
            raise ValueError(f"Unexpected settings link: {settings_path}")
        for source, target, directory in pending:
            _backup(source, backup / source.relative_to(root), directory)
        planned = sorted(set(planned) | {str(source.relative_to(root / "private")) for source, _, _ in pending})
        backup.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".manifest-", dir=backup)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(json.dumps(planned) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, manifest)
        finally:
            Path(temporary).unlink(missing_ok=True)
        for source, target in recovering:
            source.parent.mkdir(parents=True, exist_ok=True)
            _link(source, target)
        for source, target, directory in pending:
            target.parent.mkdir(parents=True, exist_ok=True)
            if directory:
                # Keep the original directory (and any open file handles) when possible.
                if target.exists():
                    original = backup / "scans-original"
                    if original.exists():
                        raise ValueError(f"An interrupted scans migration needs review: {original}")
                    source.rename(original)
                else:
                    source.rename(target)
                _link(source, target)
            else:
                if not target.exists():
                    # Old log writers keep the same inode and file lock across the move.
                    os.link(source, target)
                _link(source, target)
        if settings and not settings_path.exists():
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            with settings_path.open("x", encoding="utf-8") as stream:
                stream.write(json.dumps(settings, indent=2) + "\n")
        lessons.reconcile(history_text, root)
        return {"migrated": [str(source.relative_to(root)) for source, _, _ in pending]
                            + [str(source.relative_to(root)) for source, _ in recovering],
                "backup": str(backup)}


def cleanup_legacy_aliases(root):
    """Remove only aliases owned by this migration, never unrelated private notes."""
    root = layout_root(root)
    for old, new in {**FILES, **DIRECTORIES}.items():
        source, target = root / "private" / old, root / new
        if _alias(source, target):
            source.unlink()
    judgment = root / "private/judgment"
    if judgment.is_dir() and not judgment.is_symlink() and not any(judgment.iterdir()):
        judgment.rmdir()
    private = root / "private"
    if private.is_dir() and not private.is_symlink() and not any(private.iterdir()):
        private.rmdir()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=storage.ROOT)
    args = parser.parse_args()
    try:
        result = migrate(args.root)
    except (OSError, ValueError, SyntaxError) as error:
        parser.exit(1, f"Migration stopped: {error}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
