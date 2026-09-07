"""Install verified releases without replacing personal state."""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sys
import tempfile
import urllib.parse
import urllib.request
import uuid

VERSION = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+\Z")
RELEASES = "https://github.com/vectal-labs/director/releases"
WORKSPACE_LINKS = {name: "current/" + name for name in ("ROLE.md", "AGENTS.md", "CLAUDE.md", ".gitignore", "director", "docs", ".agents", ".claude")}
LINKS = {"CLAUDE.md": "AGENTS.md", "director/CLAUDE.md": "AGENTS.md",
         ".claude/skills": "../.agents/skills"}


def save(path, data):
    """Replace metadata atomically so an interrupted command remains recoverable."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as f:
        temporary = Path(f.name)
        json.dump(data, f, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def home():
    return Path(os.environ.get("DIRECTOR_HOME", str(Path.home() / ".local/share/director"))).expanduser().absolute()


def metadata(root):
    path = root / "install.json"
    if path.is_symlink():
        raise ValueError("The Director installation record must not be a symlink.")
    if not path.is_file():
        raise ValueError(f"No managed Director installation at {root}. Run the installer first.")
    data = json.loads(path.read_text())
    if data.get("schema") != 1 or data.get("root") != str(root) or not data.get("id"):
        raise ValueError("Unrecognized Director installation record; leaving files untouched.")
    return data


@contextlib.contextmanager
def locked(root, create=False):
    if root.is_symlink():
        raise ValueError("DIRECTOR_HOME must not be a symlink.")
    if create and not root.exists():
        root.mkdir(parents=True, mode=0o700)
    if not root.is_dir():
        raise ValueError(f"No Director installation at {root}.")
    if not (root / "install.json").exists() and any(p.name != ".lock" for p in root.iterdir()):
        raise ValueError(f"{root} already contains unmanaged files. Choose an empty DIRECTOR_HOME.")
    lock = root / ".lock"
    if lock.is_symlink():
        raise ValueError("The installation lock must not be a symlink.")
    with lock.open("a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("Another Director command is running. Try again when it finishes.") from error
        try:
            yield
        finally:
            if create and not (root / "install.json").exists():
                lock.unlink(missing_ok=True)
                if root.exists() and not any(root.iterdir()):
                    root.rmdir()


def release_url(version=None):
    if version and not VERSION.fullmatch(version):
        raise ValueError("Version must look like v1.2.3.")
    base = os.environ.get("DIRECTOR_RELEASE_BASE_URL")
    if not base:
        base = f"{RELEASES}/download/{version}" if version else f"{RELEASES}/latest/download"
    parsed = urllib.parse.urlparse(base)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}):
        raise ValueError("Release downloads require HTTPS (localhost HTTP is supported for release tests).")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Release URL must not contain credentials, query parameters, or fragments.")
    return base.rstrip("/")


class ReleaseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old = urllib.parse.urlparse(req.full_url)
        new = urllib.parse.urlparse(newurl)
        if new.scheme != "https" and not (old.scheme == "http" and new.scheme == "http" and
                                          new.hostname in {"localhost", "127.0.0.1", "::1"}):
            raise ValueError("Release download redirected to an insecure URL.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, limit):
    opener = urllib.request.build_opener(ReleaseRedirects())
    with opener.open(url, timeout=45) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Release download exceeds its size limit.")
    return data


def archive_record(checksums, version=None):
    rows = []
    for line in checksums.splitlines():
        match = re.fullmatch(r"([a-fA-F0-9]{64})\s+\*?(director-(v[0-9]+\.[0-9]+\.[0-9]+)\.tar\.gz)", line)
        if match:
            rows.append(match.groups())
    if len(rows) != 1 or (version and rows[0][2] != version):
        raise ValueError("Release checksums must name exactly one matching Director archive.")
    return rows[0]


def extract(archive, destination):
    """Accept release files and the three known internal links, never arbitrary tar paths."""
    import tarfile
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        seen = set()
        total = 0
        for member in members:
            parts = member.name.split("/")
            if member.name == "director" and member.isdir():
                continue
            if parts[0] != "director" or any(p in {"", ".", ".."} for p in parts):
                raise ValueError("Unsafe path in release archive.")
            name = "/".join(parts[1:])
            if name in seen or any("/".join(parts[1:i]) in LINKS for i in range(2, len(parts))):
                raise ValueError("Duplicate path or symlink parent in release archive.")
            seen.add(name)
            total += member.size
            if total > 100_000_000 or len(seen) > 2000:
                raise ValueError("Release archive exceeds its size limit.")
            if not (member.isfile() or member.isdir() or (member.issym() and LINKS.get(name) == member.linkname)):
                raise ValueError("Unsafe entry in release archive.")
        for member in members:
            target = destination / member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as src, target.open("xb") as dst:
                    shutil.copyfileobj(src, dst)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
        for member in members:
            if member.issym():
                target = destination / member.name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(member.linkname)
    return destination / "director"


@contextlib.contextmanager
def download(version=None):
    base = release_url(version)
    checksums = fetch(base + "/SHA256SUMS", 100_000).decode("utf-8")
    checksum, filename, expected_version = archive_record(checksums, version)
    body = fetch(base + "/" + filename, 25_000_000)
    if hashlib.sha256(body).hexdigest() != checksum.lower():
        raise ValueError("Release checksum does not match. Existing installation was not changed.")
    with tempfile.TemporaryDirectory(prefix="director-download-") as tmp:
        staging = Path(tmp)
        archive = staging / filename
        archive.write_bytes(body)
        source = extract(archive, staging)
        if (source / "VERSION").read_text().strip() != expected_version:
            raise ValueError("Release version does not match its checksum manifest.")
        yield source


def validate_source(source):
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
        if any(p in {"private", ".git", "__pycache__"} or p.startswith(".env") for p in path.relative_to(source).parts):
            raise ValueError(f"Private or generated file in release: {relative}")
        if path.suffix == ".py" and path.is_file():
            try:
                compile(path.read_bytes(), relative, "exec")
            except (SyntaxError, ValueError) as error:
                raise ValueError(f"Release contains invalid Python: {relative}") from error
    return version


def activate(root, source, data=None):
    version = validate_source(source)
    if data and data.get("removing"):
        raise ValueError("A previous uninstall was interrupted. Rerun install.sh --uninstall before installing again.")
    data = data or {"schema": 1, "root": str(root), "id": uuid.uuid4().hex,
                    "versions": [], "digests": {}, "shell": [], "installed": False}
    # Save ownership before creating install files; a failed first install can be rerun.
    save(root / "install.json", data)
    for name in ("private", "releases"):
        if (root / name).is_symlink():
            raise ValueError(f"Managed {name} directory must not be a symlink.")
        (root / name).mkdir(exist_ok=True, mode=0o700)
    cleanup_staging(root, data)
    target = root / "releases" / version
    if target.exists() or target.is_symlink():
        if version not in data["versions"] or target.is_symlink():
            raise ValueError(f"Release directory already exists and is not owned: {target}")
        validate_private_link(target)
        if tree_digest(target) != tree_digest(source):
            raise ValueError(f"Installed {version} differs from this release. Publish a new version instead.")
    else:
        staging = root / "releases" / (".staging-" + uuid.uuid4().hex)
        data.setdefault("staging", []).append(staging.name)
        if version not in data["versions"]:
            data["versions"].append(version)
        data.setdefault("digests", {})[version] = tree_digest(source)
        save(root / "install.json", data)
        try:
            shutil.copytree(source, staging, symlinks=True)
            (staging / "private").symlink_to("../../private", target_is_directory=True)
            os.replace(staging, target)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        data["staging"].remove(staging.name)
        save(root / "install.json", data)
    for name, link in WORKSPACE_LINKS.items():
        path = root / name
        if path.exists() or path.is_symlink():
            if not path.is_symlink() or os.readlink(path) != link:
                raise ValueError(f"The managed workspace path was changed: {path}")
        else:
            path.symlink_to(link)
    current = root / "current"
    if current.exists() and not current.is_symlink():
        raise ValueError("The current release path is not a managed symlink.")
    if current.is_symlink() and os.readlink(current) not in {"releases/" + v for v in data["versions"]}:
        raise ValueError("The current release link was changed outside Director.")
    temporary = root / (".current-" + uuid.uuid4().hex)
    try:
        temporary.symlink_to("releases/" + version)
        os.replace(temporary, current)
    finally:
        temporary.unlink(missing_ok=True)
    data.update(version=version, installed=True, python=str(Path(sys.executable).absolute()))
    save(root / "install.json", data)
    return data


def tree_digest(root):
    result = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        name = path.relative_to(root).as_posix()
        if name == "private" or name.startswith("private/") or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        result.update(name.encode())
        if path.is_symlink():
            result.update(os.readlink(path).encode())
        elif path.is_file():
            result.update(path.read_bytes())
    return result.hexdigest()


def install_launcher(root, data):
    binary = Path.home() / ".local/bin/director"
    binary.parent.mkdir(parents=True, exist_ok=True)
    text = ("#!/bin/sh\n# Director launcher " + data["id"] + "\nexec " + shlex.quote(data["python"]) +
            " " + shlex.quote(str(root / "current/director/cli.py")) + " --home " + shlex.quote(str(root)) + ' "$@"\n')
    if binary.exists() or binary.is_symlink():
        if binary.is_symlink() or not data.get("launcher") or binary.read_text() not in {data["launcher"]["content"], data["launcher"].get("previous_content")}:
            raise ValueError(f"{binary} belongs to another installation or was edited. Leaving it untouched.")
    previous = binary.read_text() if binary.exists() else None
    data["launcher"] = {"path": str(binary), "content": text, "previous_content": previous}
    save(root / "install.json", data)
    write_text(binary, text, mode=0o755)
    return binary


def setup_path(root, data):
    binary_dir = str(Path.home() / ".local/bin")
    if binary_dir in os.environ.get("PATH", "").split(os.pathsep):
        return
    shell = Path(os.environ.get("SHELL", "/bin/zsh")).name
    name = {"zsh": ".zshrc", "bash": ".bash_profile"}.get(shell)
    if not name:
        print(f"Add {binary_dir} to PATH to run director from any terminal.")
        return
    profile = Path.home() / name
    if profile.is_symlink():
        print(f"{profile} is a symlink; add {binary_dir} to PATH yourself.")
        return
    block = ('\n# >>> Director ' + data['id'] + '\nexport PATH="$HOME/.local/bin:$PATH"\n# <<< Director ' + data['id'] + '\n')
    original = profile.read_text() if profile.exists() else ""
    if block not in original:
        data["shell"].append({"path": str(profile), "block": block, "created": not profile.exists()})
        save(root / "install.json", data)
        write_text(profile, original + block)
    print(f"PATH configured in {profile}. Open a new terminal to use director by name.")


def check_launcher(data):
    binary = Path.home() / ".local/bin/director"
    entry = (data or {}).get("launcher")
    if entry and entry.get("path") != str(binary):
        raise ValueError("Launcher ownership record points outside the expected location.")
    if binary.exists() or binary.is_symlink():
        if binary.is_symlink() or not entry or not binary.is_file() or binary.read_text() not in {entry.get("content"), entry.get("previous_content")}:
            raise ValueError(f"{binary} belongs to another installation or was edited. Leaving it untouched.")


def cleanup_staging(root, data):
    for name in data.get("staging", []):
        if not re.fullmatch(r"\.staging-[a-f0-9]{32}", name):
            raise ValueError("Invalid staging ownership record.")
        path = root / "releases" / name
        if path.is_symlink():
            raise ValueError("The staged release directory was replaced by a symlink.")
        if path.exists():
            shutil.rmtree(path)
    data["staging"] = []
    save(root / "install.json", data)


def write_text(path, text, mode=None):
    path = Path(path)
    permissions = mode if mode is not None else (path.stat().st_mode & 0o777 if path.exists() else 0o600)
    fd, temporary = tempfile.mkstemp(prefix=".director-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), permissions)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def validate_private_link(release):
    private = release / "private"
    if not private.is_symlink() or os.readlink(private) != "../../private":
        raise ValueError(f"The private data link in {release} was changed. Move any local data out before continuing.")
