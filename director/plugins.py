#!/usr/bin/env python3
"""Run explicitly enabled local command plugins. No discovery executes plugin code."""
import argparse
from contextlib import contextmanager
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import tempfile
import time
import uuid

try:
    from .storage import ROOT
except ImportError:
    from storage import ROOT

LIMIT = 1024 * 1024
NAME = re.compile(r'[a-z][a-z0-9-]{0,63}\Z')


def decode(data):
    def invalid(value):
        raise ValueError('JSON must not contain non-finite numbers')
    try:
        value = json.loads(data, parse_constant=invalid)
        json.dumps(value, allow_nan=False)
        return value
    except RecursionError:
        raise ValueError('JSON is nested too deeply') from None


def read_json(path):
    with Path(path).open('rb') as file:
        data = file.read(LIMIT + 1)
    if len(data) > LIMIT:
        raise ValueError('JSON exceeds 1 MiB')
    return decode(data)


def enabled(root):
    path = root / 'profile/plugins.json'
    if not path.exists():
        return {}
    entries = read_json(path)
    if not isinstance(entries, dict):
        raise ValueError('profile/plugins.json must map names to absolute plugin folders')
    plugins = {}
    for name, folder in entries.items():
        if not NAME.fullmatch(name) or not isinstance(folder, str) or not Path(folder).is_absolute():
            raise ValueError('plugin names must be lowercase slugs and folders must be absolute paths')
        folder = Path(folder).resolve(strict=True)
        manifest = read_json(folder / 'plugin.json')
        if (not isinstance(manifest, dict) or set(manifest) - {'version', 'name', 'command', 'timeout_seconds'}
                or type(manifest.get('version')) is not int or manifest['version'] != 1 or manifest.get('name') != name):
            raise ValueError(f'{name}: expected a version 1 manifest with a matching name')
        command = manifest.get('command')
        timeout = manifest.get('timeout_seconds', 60)
        if not isinstance(command, list) or not command or any(not isinstance(s, str) or not s or '\0' in s for s in command):
            raise ValueError(f'{name}: command must be a nonempty argument array')
        if type(timeout) not in (int, float) or not 0 < timeout <= 300:
            raise ValueError(f'{name}: timeout_seconds must be greater than 0 and at most 300')
        skill = folder / 'SKILL.md'
        if not skill.is_file():
            raise ValueError(f'{name}: missing SKILL.md')
        plugins[name] = {**manifest, 'folder': str(folder), 'skill': str(skill), 'timeout_seconds': timeout}
    return plugins


def execute(plugin, request):
    """Bound time and output, and clean up the child process group on every exit."""
    payload = json.dumps(request, allow_nan=False).encode()
    if len(payload) > LIMIT:
        raise ValueError('request exceeds 1 MiB')
    command = list(plugin['command'])
    if command[0] == 'python3':
        command[0] = sys.executable
    with tempfile.TemporaryFile() as stdin, selectors.DefaultSelector() as selector:
        stdin.write(payload)
        stdin.seek(0)
        process = subprocess.Popen(command, cwd=plugin['folder'], stdin=stdin,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        try:
            for stream in (process.stdout, process.stderr):
                selector.register(stream, selectors.EVENT_READ)
            output, total = bytearray(), 0
            deadline = time.monotonic() + plugin['timeout_seconds']
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError('plugin timed out; do not automatically retry an apply')
                for key, _ in selector.select(min(remaining, 0.1)):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    total += len(chunk)
                    if total > LIMIT:
                        raise ValueError('plugin output exceeds 1 MiB')
                    if key.fileobj is process.stdout:
                        output.extend(chunk)
            try:
                code = process.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                raise ValueError('plugin timed out; do not automatically retry an apply') from None
            if code:
                raise ValueError(f'plugin exited with code {code}; raw output withheld')
            try:
                response = decode(output)
            except (ValueError, UnicodeError):
                raise ValueError('plugin returned invalid JSON; raw output withheld') from None
            if (not isinstance(response, dict) or set(response) != {'version', 'result'}
                    or type(response['version']) is not int or response['version'] != 1
                    or not isinstance(response['result'], dict)):
                raise ValueError('plugin response must contain version: 1 and a result object')
            return response['result']
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            process.stdout.close()
            process.stderr.close()


def save(path, record):
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.result-')
    try:
        with os.fdopen(fd, 'w') as file:
            json.dump(record, file, ensure_ascii=False, allow_nan=False, indent=2)
            file.write('\n')
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def locked(directory):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / '.lock').open('a') as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('this plugin is already running') from None
        yield


def run(root, name, operation, data, approved=False):
    if operation not in {'observe', 'apply'}:
        raise ValueError('operation must be observe or apply')
    if operation == 'apply' and not approved:
        raise ValueError('apply requires --approved after the operator approves the exact input')
    if not isinstance(data, dict):
        raise ValueError('input must be a JSON object')
    plugin = enabled(root).get(name)
    if plugin is None:
        raise ValueError('plugin is not enabled in profile/plugins.json')
    request = {'version': 1, 'id': uuid.uuid4().hex, 'operation': operation, 'input': data}
    # Validate before recording or starting anything.
    if len(json.dumps(request, allow_nan=False).encode()) > LIMIT:
        raise ValueError('request exceeds 1 MiB')
    directory = root / 'state/plugins' / name
    with locked(directory):
        path = directory / (request['id'] + '.json')
        record = {'plugin': name, 'request': request, 'approved': approved,
                  'started_at': dt.datetime.now().astimezone().isoformat(), 'status': 'started'}
        save(path, record)
        try:
            record['result'] = execute(plugin, request)
            record['status'] = 'succeeded'
        except (OSError, ValueError, KeyboardInterrupt) as error:
            record.update(status='uncertain' if operation == 'apply' else 'failed',
                          error='interrupted' if isinstance(error, KeyboardInterrupt) else str(error))
            raise
        finally:
            record['finished_at'] = dt.datetime.now().astimezone().isoformat()
            save(path, record)
    return {**record, 'record': str(path)}


def interrupted(signum, frame):
    raise KeyboardInterrupt


def main(argv=None, root=ROOT):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('list', help='list enabled plugins and their instruction paths; executes no plugins')
    for operation in ('observe', 'apply'):
        command = sub.add_parser(operation)
        command.add_argument('name')
        command.add_argument('--input', type=Path, help='JSON object file (observe defaults to {})')
        if operation == 'apply':
            command.add_argument('--approved', action='store_true', help='operator approved this exact input')
    args = parser.parse_args(argv)
    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        root = Path(root)
        if args.command == 'list':
            result = [{'name': name, 'skill': p['skill']} for name, p in enabled(root).items()]
        else:
            if args.command == 'apply' and args.input is None:
                raise ValueError('apply requires an --input file containing the exact approved changes')
            data = read_json(args.input) if args.input else {}
            result = run(root, args.name, args.command, data, getattr(args, 'approved', False))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError) as error:
        print(f'Plugin: {error}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('Plugin interrupted; check its saved record before any retry.', file=sys.stderr)
        return 130
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == '__main__':
    sys.exit(main())
