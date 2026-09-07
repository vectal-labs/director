"""Interactive setup and explicitly owned bb/cmux sessions for the installer.

The caller serializes lifecycle commands. Scanning stays in the read-only adapters.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import uuid

CMUX_BUNDLED = Path('/Applications/cmux.app/Contents/Resources/bin/cmux')
PROMPT = ('Read ROLE.md and become the Director. Read the app skill and private judgment '
          'files before your first dry run. Preserve manual approval for every agent action '
          'and keep recurring automation disabled.')


class LaunchError(RuntimeError):
    pass


def _binary(app):
    value = shutil.which(app)
    if value:
        return value
    if app == 'cmux' and CMUX_BUNDLED.is_file():
        return str(CMUX_BUNDLED)
    raise LaunchError(f'Missing {app} CLI. Install it and add it to PATH, then run director setup.')


def _run(app, *args, as_json=True):
    env = os.environ.copy()
    if app == 'bb':
        env = {key: value for key, value in env.items() if not key.startswith('CMUX_')}
    command = [_binary(app), *map(str, args)] + (['--json'] if as_json else [])
    try:
        result = subprocess.run(command, capture_output=True, text=True, env=env, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise LaunchError(f'{app} {" ".join(args[:2])} did not finish. Open {app}, then retry.') from error
    if (result.returncode and app == 'bb' and args[:2] == ('thread', 'show') and
            result.stderr.strip() == 'Error: HTTP 404: Thread not found'):
        return None
    if result.returncode:
        hint = ('Open bb and sign in to your selected provider.' if app == 'bb' else
                'Open cmux and run this command inside a cmux terminal, or allow socket access in its settings.')
        raise LaunchError(f'{app} {" ".join(args[:2])} failed (exit {result.returncode}). {hint}')
    if not as_json:
        return result.stdout
    try:
        value = json.loads(result.stdout)
    except ValueError as error:
        raise LaunchError(f'{app} returned invalid JSON. Update its CLI, then retry.') from error
    if isinstance(value, dict) and value.get('ok') is False:
        raise LaunchError(f'{app} rejected the command. Open the app and retry.')
    return value.get('result', value) if isinstance(value, dict) else value


def _load(path, default):
    if not path.exists():
        return default
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise LaunchError(f'Cannot read {path}. Restore its valid JSON before continuing.') from error
    if not isinstance(value, dict):
        raise LaunchError(f'Expected a JSON object in {path}.')
    return value


def _save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _ledger(private):
    value = _load(private / 'launches.json', {'schema': 1, 'projects': [], 'sessions': []})
    if value.get('schema') != 1 or any(not isinstance(value.get(k), list) for k in ('projects', 'sessions')):
        raise LaunchError(f'Invalid ownership records in {private / "launches.json"}.')
    return value


def _ask(question, default=''):
    try:
        with open('/dev/tty', 'r') as reader, open('/dev/tty', 'w') as writer:
            writer.write(f'{question}' + (f' [{default}]' if default else '') + ': ')
            writer.flush()
            answer = reader.readline()
    except OSError as error:
        raise LaunchError('Setup needs a terminal. Run director setup in a terminal or supply --app/--provider/--model.') from error
    if not answer:
        raise LaunchError('Setup input closed. Run director setup again in a terminal.')
    return answer.strip() or default


def _choose(label, values, selected, interactive, default=None):
    if selected:
        if selected not in values:
            raise LaunchError(f'Unavailable {label}: {selected}. Available: {", ".join(values)}.')
        return selected
    if not values:
        raise LaunchError(f'No available {label}. Install and sign in to a supported agent first.')
    default = default if default in values else values[0]
    if len(values) == 1 or not interactive:
        return default
    while True:
        answer = _ask(f'Choose {label} ({", ".join(values)})', default)
        if answer in values:
            return answer
        print(f'Choose one of: {", ".join(values)}.')


def _path_matches(value, record):
    return bool(value) and str(Path(value).absolute()) in {record['root'], record['resolved_root']}


def _sources_match(project, record):
    return any(_path_matches(source.get('path'), record) and
               (not record.get('host_id') or source.get('hostId') == record['host_id'])
               for source in project.get('sources', []))


def _bb_project(root, private):
    ledger = _ledger(private)
    projects = _run('bb', 'project', 'list')
    if not isinstance(projects, list):
        raise LaunchError('bb project list returned an unsupported response.')
    for saved in ledger['projects']:
        if saved['root'] != str(root):
            continue
        found = next((p for p in projects if p.get('id') == saved.get('id') or
                      (not saved.get('id') and p.get('name') == saved['name'])), None)
        if found:
            if not _sources_match(found, saved):
                raise LaunchError('The saved bb project now points elsewhere. Restore it before starting Director.')
            saved['id'] = found['id']
            saved['host_id'] = next(s['hostId'] for s in found['sources'] if _path_matches(s.get('path'), saved))
            _save(private / 'launches.json', ledger)
            return saved
    record = {'root': str(root), 'resolved_root': str(root.resolve())}
    matches = [p for p in projects if _sources_match(p, record)]
    if len(matches) > 1:
        raise LaunchError('Multiple bb projects use the Director folder. Keep one project source, then retry.')
    if matches:
        project = matches[0]
        record.update(id=project['id'], name=project['name'], owned=False)
    else:
        record.update(name=f'Director {uuid.uuid4().hex[:12]}', owned=True)
        ledger['projects'].append(record)
        _save(private / 'launches.json', ledger)
        project = _run('bb', 'project', 'create', '--name', record['name'], '--root', str(root))
        project = project.get('project', project)
        record['id'] = project['id']
        _save(private / 'launches.json', ledger)
    sources = [s for s in project.get('sources', []) if _path_matches(s.get('path'), record)]
    if len(sources) != 1 or not sources[0].get('hostId'):
        raise LaunchError('Cannot determine the local bb project machine. Open the project in bb and retry.')
    record['host_id'] = sources[0]['hostId']
    if not record['owned']:
        ledger['projects'].append(record)
    _save(private / 'launches.json', ledger)
    return record


def configure(app, provider, model, root: Path, private: Path, interactive=True):
    """Choose installed tooling; existing choices win unless explicitly overridden."""
    root = root.absolute()
    previous = _load(private / 'config.json', {})
    apps = []
    for candidate in ('bb', 'cmux'):
        try:
            _binary(candidate)
            apps.append(candidate)
        except LaunchError:
            pass
    chosen = app or previous.get('app')
    if len(apps) > 1 and not chosen and not interactive:
        raise LaunchError('Both bb and cmux are installed. Run director setup --app bb or --app cmux.')
    app = _choose('app', apps, chosen, interactive)
    reuse = previous if previous.get('app') == app else {}
    config = {'app': app}
    if app == 'bb':
        project = _bb_project(root, private)
        host = project['host_id']
        providers = _run('bb', 'provider', 'list', '--machine', host)
        eligible = [p['id'] for p in providers if p.get('available') and
                    'accept-edits' in p.get('capabilities', {}).get('permissionModes', [])]
        selected = _choose('provider', eligible, provider or reuse.get('provider'), interactive)
        models = _run('bb', 'provider', 'models', selected, '--machine', host)
        defaults = [m['id'] for m in models if m.get('isDefault')]
        old_model = reuse.get('model') if reuse.get('provider') == selected else None
        config.update(provider=selected, model=_choose('model', [m['id'] for m in models],
                      model or old_model, interactive, defaults[0] if defaults else None),
                      project_id=project['id'], host_id=host)
    else:
        _run('cmux', 'tree', '--all', '--id-format', 'both')
        available = [p for p in ('claude', 'codex') if shutil.which(p)]
        chosen_provider = provider or reuse.get('provider')
        if chosen_provider == 'claude-code':
            chosen_provider = 'claude'
        selected = _choose('provider', available, chosen_provider, interactive)
        old_model = reuse.get('model') if reuse.get('provider') == selected else None
        chosen_model = model or old_model
        if chosen_model is None and interactive:
            chosen_model = _ask('Model ID (leave blank for the agent default)') or None
        help_text = _run(selected, '--help', as_json=False)
        required = ['--model', '--add-dir'] + (['--sandbox', '--ask-for-approval'] if selected == 'codex'
                                               else ['--permission-mode'])
        if any(flag not in help_text for flag in required):
            raise LaunchError(f'Update {selected}; its CLI does not support the required launch flags.')
        config.update(provider=selected, model=chosen_model)
        print('cmux only sees agents with hooks. Run `cmux hooks setup` for agents you want to watch. '
              'This changes shared agent settings; Director leaves those settings untouched. '
              'Claude hooks are supplied by the cmux Claude wrapper.')
    print(f'Director will use {config["app"]} / {config["provider"]} / {config.get("model") or "agent default"}.')
    return config


def _bb_threads(project):
    rows = []
    for archived in (False, True):
        args = ['thread', 'list', '--project', project, '--include-hidden']
        if archived:
            args.append('--archived')
        response = _run('bb', *args)
        response = response.get('threads', response) if isinstance(response, dict) else response
        if not isinstance(response, list):
            raise LaunchError('bb thread list returned an unsupported response.')
        rows.extend(response)
    return rows


def _bb_live(record):
    if record.get('id'):
        result = _run('bb', 'thread', 'show', record['id'])
    else:
        # A timeout can lose a successful spawn response. Recover only the exact
        # initial prompt's random marker, never another thread named DIRECTOR.
        matches = []
        for row in _bb_threads(record['project_id']):
            if row.get('title') != record['title']:
                continue
            history = _run('bb', 'thread', 'history', row['id'])
            if any(record['marker'] in item.get('text', '')
                   for message in history for item in message.get('input', [])):
                matches.append(row)
        if len(matches) > 1:
            raise LaunchError('Multiple bb threads match a pending Director launch. Close them manually before retrying.')
        result = _run('bb', 'thread', 'show', matches[0]['id']) if matches else None
    if result is None:
        return None
    thread = result.get('thread', result)
    environment = result.get('environment', {})
    if (thread.get('projectId') != record['project_id'] or thread.get('title') != record['title'] or
            environment.get('hostId') != record['host_id'] or not _path_matches(environment.get('path'), record)):
        raise LaunchError(f'Refusing to touch bb thread {thread.get("id")}: its ownership metadata changed. '
                          'Close that session manually, then retry.')
    record['id'] = thread['id']
    return None if thread.get('deletedAt') else thread


def _cmux_workspaces():
    tree = _run('cmux', 'tree', '--all', '--id-format', 'both')
    if not isinstance(tree, dict) or not isinstance(tree.get('windows'), list):
        raise LaunchError('cmux tree returned an unsupported response.')
    return [workspace for window in tree['windows'] for workspace in window.get('workspaces', [])]


def _identifier(item, kind):
    return item.get(f'{kind}_id') or item.get('uuid') or item.get('id')


def _cmux_live(record):
    found = next((w for w in _cmux_workspaces() if _identifier(w, 'workspace') == record.get('id') or
                  (not record.get('id') and (w.get('title') or w.get('name')) == record['title'])), None)
    if found is None:
        return None
    surfaces = [s for pane in found.get('panes', []) for s in pane.get('surfaces', [])]
    if ((found.get('title') or found.get('name')) != record['title'] or len(surfaces) != 1 or
            surfaces[0].get('type') != 'terminal' or
            (record.get('surface_id') and _identifier(surfaces[0], 'surface') != record['surface_id'])):
        raise LaunchError(f'Refusing to close cmux workspace {record.get("id")}: its layout or title changed. '
                          'Close the Director workspace manually, then retry.')
    if found.get('cwd') and not _path_matches(found['cwd'], record):
        raise LaunchError('The Director cmux workspace now points elsewhere. Close it manually before retrying.')
    record['id'] = _identifier(found, 'workspace')
    record['surface_id'] = _identifier(surfaces[0], 'surface')
    if not record['id'] or not record['surface_id']:
        raise LaunchError('cmux did not provide workspace and terminal UUIDs.')
    return found


def start(config: dict, root: Path, private: Path) -> None:
    root = root.absolute()
    if config.get('app') not in ('bb', 'cmux'):
        raise LaunchError('Run director setup before director start.')
    ledger = _ledger(private)
    for record in ledger['sessions']:
        if record['app'] != config['app'] or record.get('stopped'):
            continue
        live = _bb_live(record) if record['app'] == 'bb' else _cmux_live(record)
        _save(private / 'launches.json', ledger)
        if live:
            if record.get('phase') != 'started':
                raise LaunchError('A previous launch was interrupted. Close its Director session in the app, then retry.')
            if record['app'] == 'bb' and live.get('status') == 'error':
                raise LaunchError(f'Director thread {record["id"]} needs attention. Open it in bb and retry its failed turn.')
            print(f'Director is already open in {record["app"]}: {record["id"]}.')
            return
        record['stopped'] = True
    record = {'app': config['app'], 'root': str(root), 'resolved_root': str(root.resolve()),
              'title': f'Director {uuid.uuid4().hex[:12]}', 'phase': 'creating'}
    if record['app'] == 'bb':
        record.update(project_id=config['project_id'], host_id=config['host_id'],
                      title='DIRECTOR', marker=f'Managed Director launch: {uuid.uuid4()}.')
    ledger['sessions'].append(record)
    _save(private / 'launches.json', ledger)
    if record['app'] == 'bb':
        result = _run('bb', 'thread', 'spawn', '--project', config['project_id'],
                      '--environment', str(root), '--machine', config['host_id'],
                      '--provider', config['provider'], '--model', config['model'],
                      '--permission-mode', 'accept-edits', '--title', record['title'],
                      '--prompt', PROMPT + '\n' + record['marker'])
        thread = result.get('thread', result)
        record['id'] = thread.get('id') or result.get('threadId')
        _save(private / 'launches.json', ledger)
        if not record['id'] or not _bb_live(record):
            raise LaunchError('bb did not confirm the new Director thread. Check the app before retrying.')
    else:
        result = _run('cmux', 'new-workspace', '--name', record['title'], '--cwd', str(root), '--focus', 'false')
        record['id'] = _identifier(result, 'workspace')
        _save(private / 'launches.json', ledger)
        if not _cmux_live(record):
            raise LaunchError('cmux did not confirm the Director workspace. Check the app before retrying.')
        _save(private / 'launches.json', ledger)
        provider = config['provider']
        if provider not in ('claude', 'codex'):
            raise LaunchError('Run director setup and select Claude or Codex for cmux.')
        command = [provider, '--add-dir', str(private)]
        if provider == 'codex':
            command += ['--sandbox', 'workspace-write', '--ask-for-approval', 'on-request']
        else:
            command += ['--permission-mode', 'acceptEdits']
        if config.get('model'):
            command += ['--model', config['model']]
        command.append(PROMPT)
        # Keep the shell's cmux agent wrappers, which supply Claude's shared hooks.
        text = 'unset BB_THREAD_ID BB_PROJECT_ID BB_ENVIRONMENT_ID; ' + shlex.join(command)
        record['phase'] = 'sending'
        _save(private / 'launches.json', ledger)
        _run('cmux', 'send', '--workspace', record['id'], '--surface', record['surface_id'], '--', text, as_json=False)
        _run('cmux', 'send-key', '--workspace', record['id'], '--surface', record['surface_id'], 'enter', as_json=False)
    record['phase'] = 'started'
    _save(private / 'launches.json', ledger)
    print(f'Director started in {record["app"]}: {record["id"]}. Open that session to finish any provider sign-in or trust prompt.')


def stop_owned(root: Path, private: Path, purge=False) -> None:
    """Fail closed: the installer must retain files when this cannot verify a stop."""
    ledger = _ledger(private)
    for record in ledger['sessions']:
        if record.get('purged'):
            continue
        if record['app'] == 'bb':
            live = _bb_live(record)
            if live:
                _run('bb', 'thread', 'stop', record['id'])
                live = _bb_live(record)
                if live and live.get('status') not in ('idle', 'error'):
                    raise LaunchError(f'bb thread {record["id"]} is still stopping. Wait for it to stop, then retry uninstall.')
                if purge:
                    _run('bb', 'thread', 'delete', record['id'], '--yes')
                    if _bb_live(record):
                        raise LaunchError('bb still reports the Director thread after deletion. Retry uninstall.')
        elif record['app'] == 'cmux':
            if _cmux_live(record):
                _run('cmux', 'close-workspace', '--workspace', record['id'])
                if _cmux_live(record):
                    raise LaunchError('cmux still reports the Director workspace. Close it manually, then retry uninstall.')
        else:
            raise LaunchError('Unknown app in Director ownership records. Installation has been preserved.')
        record['stopped'] = True
        record['purged'] = bool(purge)
        _save(private / 'launches.json', ledger)
    if purge:
        for project in ledger['projects']:
            if not project.get('owned') or project.get('purged'):
                continue
            existing = _run('bb', 'project', 'list')
            current = next((p for p in existing if p.get('id') == project.get('id') or
                            (not project.get('id') and p.get('name') == project['name'])), None)
            if current:
                if (not _sources_match(current, project) or current.get('name') != project['name'] or
                        len(current.get('sources', [])) != 1):
                    raise LaunchError('The Director bb project changed. Remove its registration manually before purging.')
                if _bb_threads(current['id']):
                    raise LaunchError('The Director bb project contains other threads. Move them to another project before purging.')
                _run('bb', 'project', 'delete', current['id'], '--yes')
            project['purged'] = True
            _save(private / 'launches.json', ledger)
