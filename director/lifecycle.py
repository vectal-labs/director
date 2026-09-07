"""Managed update and removal; personal data is purged only on explicit request."""
import os
from pathlib import Path
import shutil

import install
import launch


def update(root, version=None):
    data = install.metadata(root)
    with install.download(version) as source:
        offered = install.validate_source(source)
        if not version and data.get("version") and tuple(map(int, offered[1:].split('.'))) < tuple(map(int, data['version'][1:].split('.'))):
            raise ValueError("Latest release is older than the installed version. Use --version to request a downgrade.")
        install.check_launcher(data)
        data = install.activate(root, source, data)
        install.install_launcher(root, data)
    print(f"Director {data['version']} is installed. Existing sessions keep their current version; new starts use this release.")


def uninstall(root, purge=False):
    data = install.metadata(root)
    install.check_launcher(data)
    # Validate every destructive target before stopping sessions or removing anything.
    for version in data.get("versions", []):
        if not install.VERSION.fullmatch(version):
            raise ValueError("Invalid installed release record.")
        target = root / "releases" / version
        if target.is_symlink() or (target.exists() and not target.is_dir()):
            raise ValueError(f"Release path changed outside Director: {target}")
        removing = version in data.get("removing", [])
        if target.exists() and (not removing or (target / "private").exists() or (target / "private").is_symlink()):
            install.validate_private_link(target)
        expected = data.get("digests", {}).get(version)
        if target.exists() and not removing and (not expected or install.tree_digest(target) != expected):
            raise ValueError(f"Local changes found in {target}. Move those changes out before uninstalling.")
    if (root / "releases").is_symlink() or (root / "private").is_symlink():
        raise ValueError("Managed data directories must not be symlinks.")
    current = root / "current"
    if current.exists() or current.is_symlink():
        if not current.is_symlink() or os.readlink(current) not in {"releases/" + v for v in data.get("versions", [])}:
            raise ValueError("The current release path was changed outside Director.")
    for name, link in install.WORKSPACE_LINKS.items():
        path = root / name
        if (path.exists() or path.is_symlink()) and (not path.is_symlink() or os.readlink(path) != link):
            raise ValueError(f"The managed workspace path was changed: {path}")
    for item in data.get("shell", []):
        path = Path(item["path"])
        if path not in {Path.home() / ".zshrc", Path.home() / ".bash_profile"} or path.is_symlink():
            raise ValueError("Shell profile ownership cannot be verified; leaving the installation intact.")
        if path.exists() and item["block"] not in path.read_text() and data["id"] in path.read_text():
            raise ValueError(f"The Director PATH block in {path} was edited. Restore it before uninstalling.")
    launch.stop_owned(root, root / "private", purge=purge)
    install.cleanup_staging(root, data)
    for item in data.get("shell", []):
        path = Path(item["path"])
        if path.exists():
            remaining = path.read_text().replace(item["block"], "", 1)
            if not remaining and item["created"]:
                path.unlink()
            else:
                install.write_text(path, remaining)
    binary = data.get("launcher")
    if binary:
        Path(binary["path"]).unlink(missing_ok=True)
    for name in install.WORKSPACE_LINKS:
        (root / name).unlink(missing_ok=True)
    current.unlink(missing_ok=True)
    data["removing"] = list(data.get("versions", []))
    install.save(root / "install.json", data)
    for version in data.get("versions", []):
        target = root / "releases" / version
        if target.exists():
            shutil.rmtree(target)
    releases = root / "releases"
    if releases.exists() and not any(releases.iterdir()):
        releases.rmdir()
    data.update(installed=False, versions=[], digests={}, shell=[], launcher=None, removing=[])
    install.save(root / "install.json", data)
    if purge:
        private = root / "private"
        if private.exists():
            shutil.rmtree(private)
        (root / "install.json").unlink()
        (root / ".lock").unlink(missing_ok=True)
        if not any(root.iterdir()):
            root.rmdir()
        else:
            print(f"Unrecognized files remain in {root}; they were kept.")
        print("Director and its personal rules, logs, and scans were removed.")
    else:
        print(f"Director removed. Personal data kept at {root / 'private'}.")
        print("To remove that data later, rerun the installer with --uninstall --purge.")
    print("Shared bb/cmux installations, hooks, and provider chat history are managed by those apps.")
