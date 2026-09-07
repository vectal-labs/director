#!/bin/sh
# Install a verified Director release. Safe to download and inspect before running.
set -eu

director_main() {
    if [ "$(uname -s)" != Darwin ]; then
        echo 'Director requires macOS.' >&2
        return 1
    fi
    director_python=${DIRECTOR_PYTHON:-python3}
    if ! command -v "$director_python" >/dev/null 2>&1 || ! "$director_python" -c 'import sys; sys.exit(sys.version_info < (3, 9))'; then
        echo 'Director requires Python 3.9+. Install Python, then run this command again.' >&2
        return 1
    fi
    if ! command -v curl >/dev/null 2>&1; then
        echo 'Director requires curl.' >&2
        return 1
    fi
    director_version=
    director_uninstall=false
    director_purge=false
    # Version belongs to the downloader; the remaining flags are forwarded to setup.
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --version)
                [ "$#" -ge 2 ] || { echo '--version requires v1.2.3.' >&2; return 1; }
                director_version=$2
                shift 2
                ;;
            --uninstall) director_uninstall=true; shift ;;
            --purge) director_purge=true; shift ;;
            *) break ;;
        esac
    done
    director_home=${DIRECTOR_HOME:-"$HOME/.local/share/director"}
    if [ "$director_uninstall" = true ] && [ -f "$director_home/current/director/cli.py" ]; then
        if [ "$director_purge" = true ]; then
            "$director_python" "$director_home/current/director/cli.py" --home "$director_home" uninstall --purge
        else
            "$director_python" "$director_home/current/director/cli.py" --home "$director_home" uninstall
        fi
        return
    fi
    if [ "$director_purge" = true ] && [ "$director_uninstall" != true ]; then
        echo '--purge requires --uninstall.' >&2
        return 1
    fi
    director_base=${DIRECTOR_RELEASE_BASE_URL:-}
    if [ -z "$director_base" ]; then
        if [ -n "$director_version" ]; then
            director_base="https://github.com/vectal-labs/director/releases/download/$director_version"
        else
            director_base='https://github.com/vectal-labs/director/releases/latest/download'
        fi
    fi
    "$director_python" - "$director_base" "$director_version" <<'PY'
import re, sys, urllib.parse
url = urllib.parse.urlparse(sys.argv[1])
if (url.scheme != 'https' and not (url.scheme == 'http' and url.hostname in {'localhost', '127.0.0.1', '::1'})) or url.username or url.password or url.query or url.fragment:
    sys.exit('Release URL must use HTTPS (localhost HTTP is allowed for release tests).')
if sys.argv[2] and not re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+', sys.argv[2]):
    sys.exit('Version must look like v1.2.3.')
PY
    director_tmp=$(mktemp -d "${TMPDIR:-/tmp}/director-install.XXXXXXXX")
    trap 'rm -rf "$director_tmp"' EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM HUP
    director_base=${director_base%/}
    # HTTPS redirects stay HTTPS. Local HTTP is reserved for fixture release servers.
    case "$director_base" in
        https:*) director_protocol='=https' ;;
        *) director_protocol='=http,https' ;;
    esac
    curl --fail --silent --show-error --location --proto "$director_protocol" --proto-redir "$director_protocol" --connect-timeout 15 --max-time 120 --max-filesize 100000 "$director_base/SHA256SUMS" -o "$director_tmp/SHA256SUMS"
    director_archive=$("$director_python" - "$director_tmp/SHA256SUMS" "$director_version" <<'PY'
import pathlib, re, sys
matches = []
for line in pathlib.Path(sys.argv[1]).read_text().splitlines():
    m = re.fullmatch(r'([a-fA-F0-9]{64})\s+\*?(director-(v[0-9]+\.[0-9]+\.[0-9]+)\.tar\.gz)', line)
    if m:
        matches.append(m.groups())
if len(matches) != 1 or (sys.argv[2] and matches[0][2] != sys.argv[2]):
    sys.exit('Release checksums must name exactly one matching Director archive.')
print(matches[0][1])
PY
)
    curl --fail --silent --show-error --location --proto "$director_protocol" --proto-redir "$director_protocol" --connect-timeout 15 --max-time 120 --max-filesize 25000000 "$director_base/$director_archive" -o "$director_tmp/$director_archive"
    "$director_python" - "$director_tmp" "$director_archive" <<'PY'
import hashlib, pathlib, re, shutil, sys, tarfile
root = pathlib.Path(sys.argv[1])
archive = root / sys.argv[2]
expected = next(line.split()[0] for line in (root / 'SHA256SUMS').read_text().splitlines() if line.split()[-1].lstrip('*') == archive.name)
if hashlib.sha256(archive.read_bytes()).hexdigest() != expected.lower():
    sys.exit('Release checksum does not match. Nothing was installed.')
links = {'CLAUDE.md': 'AGENTS.md', 'director/CLAUDE.md': 'AGENTS.md', '.claude/skills': '../.agents/skills'}
with tarfile.open(archive, 'r:gz') as tar:
    members = tar.getmembers()
    seen = set()
    total = 0
    for member in members:
        parts = member.name.split('/')
        if member.name == 'director' and member.isdir():
            continue
        if parts[0] != 'director' or any(p in {'', '.', '..'} for p in parts):
            sys.exit('Unsafe path in release archive.')
        name = '/'.join(parts[1:])
        if name in seen or any('/'.join(parts[1:i]) in links for i in range(2, len(parts))):
            sys.exit('Duplicate path or symlink parent in release archive.')
        seen.add(name)
        total += member.size
        if total > 100000000 or len(seen) > 2000:
            sys.exit('Release archive exceeds its size limit.')
        if not (member.isfile() or member.isdir() or (member.issym() and links.get(name) == member.linkname)):
            sys.exit('Unsafe entry in release archive.')
    for member in members:
        target = root / member.name
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
        elif member.isfile():
            target.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(member) as src, target.open('xb') as dst:
                shutil.copyfileobj(src, dst)
            target.chmod(0o755 if member.mode & 0o111 else 0o644)
    for member in members:
        if member.issym():
            target = root / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(member.linkname)
version = re.fullmatch(r'director-(v[0-9]+\.[0-9]+\.[0-9]+)\.tar\.gz', archive.name).group(1)
if (root / 'director/VERSION').read_text().strip() != version:
    sys.exit('Release VERSION does not match the checksum manifest.')
PY
    if [ "$director_uninstall" = true ]; then
        if [ "$director_purge" = true ]; then
            "$director_python" "$director_tmp/director/director/cli.py" --home "$director_home" uninstall --purge
        else
            "$director_python" "$director_tmp/director/director/cli.py" --home "$director_home" uninstall
        fi
    else
        "$director_python" "$director_tmp/director/director/cli.py" --home "$director_home" install --source "$director_tmp/director" "$@"
    fi
}
# A function keeps a partially downloaded installer from executing before its end.
director_main "$@"
