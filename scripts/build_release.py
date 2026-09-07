#!/usr/bin/env python3
"""Build reproducible Director release assets from an explicit source allowlist."""

import argparse
import gzip
import hashlib
import io
from pathlib import Path
import re
import shutil
import stat
import tarfile
import tempfile


ROOT = Path(__file__).resolve().parents[1]
VERSION_PATTERN = re.compile(r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
RUNTIME_FILES = (
    "director/bb_app.py",
    "director/cli.py",
    "director/cmux_app.py",
    "director/install.py",
    "director/launch.py",
    "director/lessons.py",
    "director/lifecycle.py",
    "director/log.py",
    "director/memory.py",
    "director/preferences.py",
    "director/storage.py",
    "director/migrate.py",
    "director/scan.py",
    "director/setup.py",
)
INSTRUCTION_FILES = (
    ".gitignore",
    "AGENTS.md",
    "ROLE.md",
    "director/AGENTS.md",
    "docs/memory.md",
    "docs/profile.md",
    ".agents/skills/director-bb/SKILL.md",
    ".agents/skills/director-cmux/SKILL.md",
)
LINKS = {
    "CLAUDE.md": "AGENTS.md",
    "director/CLAUDE.md": "AGENTS.md",
    ".claude/skills": "../.agents/skills",
}


def validate_version(version):
    if not VERSION_PATTERN.fullmatch(version) or len(version) > 100:
        raise ValueError("version must be a stable tag such as v0.1.0")


def source_path(source, relative, *, link=False):
    """Do not follow source symlinks, including symlinked parent folders."""
    path = source
    parts = Path(relative).parts
    for index, part in enumerate(parts):
        path = path / part
        info = path.lstat()
        last = index == len(parts) - 1
        if stat.S_ISLNK(info.st_mode) and not (last and link):
            raise ValueError(f"source symlinks are not allowed: {relative}")
        if not last and not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"source parent must be a directory: {relative}")
    return path


def read_source(source, relative):
    path = source_path(source, relative)
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError(f"source must be a regular file: {relative}")
    return path.read_bytes()


def archive_entries(source, version):
    entries = {}
    for relative in RUNTIME_FILES + INSTRUCTION_FILES:
        entries[relative] = ("file", read_source(source, relative))
    entries["VERSION"] = ("file", (version + "\n").encode("ascii"))
    for relative, target in LINKS.items():
        path = source_path(source, relative, link=True)
        if not path.is_symlink() or str(path.readlink()) != target:
            raise ValueError(f"expected source symlink {relative} -> {target}")
        # Every accepted target is fixed above and included in the archive.
        entries[relative] = ("link", target)
    return entries


def write_archive(path, entries):
    directories = {"director"}
    for relative in entries:
        parent = Path("director") / relative
        directories.update(str(item) for item in parent.parents if str(item) != ".")
    with path.open("xb") as raw:
        # Empty filename and a fixed timestamp make gzip independent of its path.
        with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                for name in sorted(directories):
                    info = tarfile.TarInfo(name)
                    info.type = tarfile.DIRTYPE
                    info.mode = 0o755
                    archive.addfile(info)
                for relative, (kind, content) in sorted(entries.items()):
                    info = tarfile.TarInfo(f"director/{relative}")
                    if kind == "link":
                        info.type = tarfile.SYMTYPE
                        info.linkname = content
                        info.mode = 0o777
                        archive.addfile(info)
                    else:
                        info.mode = 0o644
                        info.size = len(content)
                        archive.addfile(info, io.BytesIO(content))


def build_release(source, version, output):
    validate_version(version)
    source = Path(source).resolve(strict=True)
    output = Path(output).absolute()
    if not source.is_dir():
        raise ValueError("source must be a directory")
    if output.exists() or output.is_symlink():
        raise ValueError(f"output already exists; choose a new directory: {output}")
    if not output.parent.is_dir():
        raise ValueError(f"output parent directory does not exist: {output.parent}")

    entries = archive_entries(source, version)
    installer = read_source(source, "install.sh")
    archive_name = f"director-{version}.tar.gz"
    # Build privately, then rename the complete directory into place.
    staging = Path(tempfile.mkdtemp(prefix=".director-release-", dir=output.parent))
    try:
        write_archive(staging / archive_name, entries)
        (staging / "install.sh").write_bytes(installer)
        (staging / "install.sh").chmod(0o755)
        checksums = "".join(
            f"{hashlib.sha256((staging / name).read_bytes()).hexdigest()}  {name}\n"
            for name in (archive_name, "install.sh")
        )
        (staging / "SHA256SUMS").write_text(checksums, encoding="ascii")
        if output.exists() or output.is_symlink():
            raise ValueError(f"output appeared during build: {output}")
        staging.rename(output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="release tag, e.g. v0.1.0")
    parser.add_argument("--output", required=True, type=Path, help="new output directory")
    args = parser.parse_args()
    try:
        output = build_release(ROOT, args.version, args.output)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Could not build release: {error}\n")
    print(f"Built {output / ('director-' + args.version + '.tar.gz')}")
    print(f"Checksums: {output / 'SHA256SUMS'}")
    print(f"Installer: {output / 'install.sh'}")


if __name__ == "__main__":
    main()
