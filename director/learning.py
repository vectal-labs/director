#!/usr/bin/env python3
"""Read current and previous sessions; retain learning questions across restarts.

App reads are separate from intervention scans. No command sends a message,
changes review eligibility, or promotes an inferred preference into the profile.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import datetime as dt
import fcntl
import json
import math
import os
import subprocess
from pathlib import Path
import uuid

try:
    from . import scan, storage
except ImportError:
    import scan
    import storage


LEARNING_DIR = storage.STATE / 'learning'


def now():
    return dt.datetime.now().astimezone().isoformat()


def read_questions():
    path = LEARNING_DIR / 'questions.jsonl'
    questions = {}
    if not path.exists():
        return questions
    for line in path.read_text().splitlines():
        event = json.loads(line)
        identity = event['id']
        if event['kind'] == 'question':
            if identity in questions or event.get('app') not in scan.APPS:
                raise ValueError('invalid or duplicate learning question')
            questions[identity] = {**event, 'status': 'open'}
        elif event['kind'] in {'answered', 'dismissed'}:
            if identity not in questions or questions[identity]['status'] != 'open':
                raise ValueError('invalid learning question resolution')
            questions[identity].update(status=event['kind'], **{
                k: v for k, v in event.items() if k not in {'id', 'kind'}})
        else:
            raise ValueError('unknown learning journal event')
    return questions


@contextmanager
def locked():
    LEARNING_DIR.mkdir(parents=True, exist_ok=True)
    with (LEARNING_DIR / '.lock').open('a') as file:
        fcntl.flock(file, fcntl.LOCK_EX)
        yield


def append(event):
    path = LEARNING_DIR / 'questions.jsonl'
    needs_newline = path.exists() and path.stat().st_size > 0
    with path.open('a+', encoding='utf-8') as file:
        if needs_newline:
            file.seek(0)
            needs_newline = not file.read().endswith('\n')
        file.write(('\n' if needs_newline else '') + json.dumps(event, ensure_ascii=False) + '\n')
        file.flush()
        os.fsync(file.fileno())


def observe(args, app):
    module = scan.APPS[app]
    rows = module.learning_sessions(os.environ[module.SELF_VAR], args.project, args.session)
    if args.session and not rows:
        raise ValueError('session is not in the visible launch-app inventory on this host')
    if any(type(r.get('updated')) not in (float, int) or not math.isfinite(r['updated']) for r in rows):
        raise ValueError('session inventory contains an invalid update time')
    rows.sort(key=lambda row: (row['updated'], row['id']), reverse=True)
    selected = rows[:args.limit]
    sessions, errors = [], []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [(row, pool.submit(module.learning_context, row, args.messages)) for row in selected]
        for row, future in futures:
            try:
                sessions.append(future.result())
            except module.ERRORS as error:
                errors.append({'id': row['id'], 'error': type(error).__name__, 'detail': str(error)})
    with locked():
        questions = [q for q in read_questions().values() if q['app'] == app]
        path = LEARNING_DIR / f'{uuid.uuid4().hex}.json'
        result = {'app': app, 'observed_at': now(), 'snapshot': str(path.relative_to(storage.ROOT)),
                  'purpose': 'learning_only', 'sessions': sessions,
                  'questions': [q for q in questions if not args.project or q['project'] == args.project],
                  'pending_question': next((q['id'] for q in questions if q['status'] == 'open'), None),
                  'listed': len(rows), 'selected': len(selected), 'limited': len(rows) > len(selected),
                  'coverage': 'partial' if errors else 'selected_sessions', 'errors': errors}
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    return result


def record_question(args, app):
    path = Path(args.snapshot)
    if not path.is_absolute():
        path = storage.ROOT / path
    if path.resolve().parent != LEARNING_DIR.resolve() or path.suffix != '.json':
        raise ValueError('use a learning snapshot from state/learning/')
    snapshot = json.loads(path.read_text())
    if snapshot['app'] != app or snapshot.get('purpose') != 'learning_only':
        raise ValueError('question source must belong to the launch app')
    matches = [(s, m) for s in snapshot['sessions'] for m in s['messages'] if m['source'] == args.source]
    if len(matches) != 1:
        raise ValueError('question source was not found uniquely in the snapshot')
    session, message = matches[0]
    if message['role'] not in {'human', 'context_unattributed'}:
        raise ValueError('question needs human evidence or an explicitly unattributed transcript')
    with locked():
        questions = read_questions().values()
        if any(q['app'] == app and q['status'] == 'open' for q in questions):
            raise ValueError('a learning question is already open; answer or dismiss it first')
        if any(q['source'] == args.source for q in questions):
            raise ValueError('this source already has a learning question; read its recorded answer or dismissal')
        event = {'kind': 'question', 'id': uuid.uuid4().hex, 'app': app, 'at': now(),
                 'session': session['id'], 'project': session['project'], 'title': session['title'],
                 'source': args.source, 'snapshot': str(path.relative_to(storage.ROOT)),
                 'evidence': message, 'question': args.question}
        append(event)
    return event


def resolve(args, app):
    with locked():
        question = read_questions().get(args.id)
        if not question or question['app'] != app:
            raise ValueError('no such question in this launch app')
        if question['status'] != 'open':
            raise ValueError('question is already resolved')
        event = {'kind': 'answered' if args.command == 'answer' else 'dismissed',
                 'id': args.id, 'resolved_at': now(),
                 'answer' if args.command == 'answer' else 'reason': args.text}
        append(event)
    return event


def positive(value):
    count = int(value)
    if count <= 0:
        raise argparse.ArgumentTypeError('must be positive')
    return count


def nonempty(value):
    if not value.strip():
        raise argparse.ArgumentTypeError('must not be empty')
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', choices=sorted(scan.APPS))
    commands = parser.add_subparsers(dest='command', required=True)
    observe_parser = commands.add_parser('observe', help='load current and historical conversation context')
    observe_parser.add_argument('--project', help='exact project ID (bb) or cwd (cmux)')
    observe_parser.add_argument('--session', help='exact thread ID (bb) or session ID/review key (cmux)')
    observe_parser.add_argument('--limit', type=positive, default=12, help='maximum sessions, newest first (12)')
    observe_parser.add_argument('--messages', type=positive, default=40, help='recent messages per bb session (40)')
    ask = commands.add_parser('ask', help='save one question before presenting it to the operator here')
    ask.add_argument('--snapshot', required=True)
    ask.add_argument('--source', required=True)
    ask.add_argument('--question', required=True, type=nonempty)
    for name in ('answer', 'dismiss'):
        sub = commands.add_parser(name)
        sub.add_argument('--id', required=True)
        sub.add_argument('--text', required=True, type=nonempty)
    args = parser.parse_args()
    try:
        app = scan.launch_app(args.app)
        if args.command == 'observe':
            result = observe(args, app)
        elif args.command == 'ask':
            result = record_question(args, app)
        else:
            result = resolve(args, app)
    except (ValueError, OSError, KeyError, TypeError, RuntimeError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f'learning failed: {error}\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
