"""Read stored teaching without migrations, repairs, app access, or inference."""
import datetime as dt
import hashlib
import json
from pathlib import Path
import re

try:
    from .. import lessons
except ImportError:
    import lessons


PROFILE_FILES = ('qa.md', 'what.md', 'how.md', 'limits.md')


def read_file(root, relative):
    path = root / relative
    if not path.resolve().is_relative_to(root):
        raise ValueError('file points outside the profile root')
    try:
        return path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return ''


def sections(text):
    """Split headings outside fenced code; retain preambles and source text."""
    lines = text.splitlines(keepends=True)
    starts, fence = [], None
    for index, line in enumerate(lines):
        marker = re.match(r'^\s{0,3}(`{3,}|~{3,})', line)
        if marker:
            token = marker[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            continue
        if fence is None:
            heading = re.match(r'^#{1,6}\s+(.+?)\s*#*\s*$', line)
            if heading:
                starts.append((index, heading[1]))
    if not starts or starts[0][0] > 0:
        starts.insert(0, (0, 'Notes'))
    for i, (start, title) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(lines)
        raw = ''.join(lines[start:end]).strip()
        body = ''.join(lines[start + (1 if lines and lines[start].startswith('#') else 0):end]).strip()
        if body:
            yield title, body, raw, start + 1


def record(identity, title, kind):
    return dict(id=identity, title=title, kind=kind, evidence=[], interpretations=[],
                locations=[], related=[], scope=None, status='Not recorded', conditions={},
                source=None, source_date=None, stored_at=None)


def source_date(source):
    match = re.search(r'\b(\d{4}-\d{2}-\d{2}|\d{2}-\d{2}-\d{4})\b', source or '')
    if not match:
        return None
    value = match[1]
    try:
        return dt.datetime.strptime(value, '%Y-%m-%d' if value[4] == '-' else '%d-%m-%Y').date().isoformat()
    except ValueError:
        return None


def location(path, raw, line=None, recorded_at=None):
    return dict(path=path, raw=raw, line=line, recorded_at=recorded_at)


def read_markdown(root, records, warnings):
    qa, pending = {}, []
    used = set()
    for name in PROFILE_FILES:
        relative = 'profile/' + name
        try:
            text = read_file(root, relative)
        except (OSError, ValueError) as error:
            warnings.append(f'{relative}: {error}')
            continue
        for title, body, raw, line in sections(text):
            numbered = re.match(r'^(?:Q)?(\d+)[.:\s]+(.+)$', title) if name == 'qa.md' else None
            key = 'Q' + numbered[1] if numbered else None
            identity = f'{relative}:{key or hashlib.sha256(title.encode()).hexdigest()[:12]}'
            if identity in used:
                identity += f':{line}'
            used.add(identity)
            item = record(identity, numbered[2] if numbered else title, 'Teaching' if key else 'Saved guidance')
            item['locations'].append(location(relative, raw, line))
            if name == 'qa.md':
                parts = re.split(r'^Source:\s*', body, maxsplit=1, flags=re.M)
                words = re.sub(r'^David(?:[’\']s exact words)?\s*:\s*', '', parts[0]).strip()
                item['evidence'].append(dict(text=words, label='Recorded teaching', path=relative))
                item['source'] = parts[1].strip() if len(parts) > 1 else None
                item['source_date'] = source_date(item['source'])
                if key:
                    qa.setdefault(key, []).append(item)
                records.append(item)
            else:
                item['interpretations'].append(dict(text=body, path=relative, label=title))
                refs = set(re.findall(r'\bQ\d+\b', title + '\n' + body))
                pending.append((item, refs))
    for item, refs in pending:
        targets = [qa[ref][0] for ref in sorted(refs) if len(qa.get(ref, [])) == 1]
        if targets and all(len(qa.get(ref, [])) == 1 for ref in refs):
            for target in targets:
                target['interpretations'].extend(item['interpretations'])
                target['locations'].extend(item['locations'])
        else:
            records.append(item)
    for key, items in qa.items():
        if len(items) > 1:
            warnings.append(f'{key}: duplicate teaching ID; records kept separate')
    return qa


def read_journals(root, records, qa, warnings, now):
    paths = ('profile/lessons.jsonl', 'state/lessons.jsonl')
    try:
        for path in paths:
            read_file(root, path)
        journal = lessons.load(root)
    except (ValueError, OSError, KeyError, TypeError) as error:
        warnings.append(f'Lesson journals could not be interpreted: {error}')
        for path in paths:
            try:
                raw = read_file(root, path)
                if raw:
                    item = record(path, path, 'Unreadable journal')
                    item['locations'].append(location(path, raw))
                    records.append(item)
            except (ValueError, OSError):
                continue
        return
    for identity, entry in journal.items():
        event, ending = entry['source'], entry['ending']
        lesson = event['lesson']
        path = paths[0] if lesson['kind'] in lessons.DURABLE_KINDS else paths[1]
        item = record('lesson:' + identity, lesson['interpretation'], lesson['kind'].replace('_', ' '))
        item.update(scope=lesson['scope'], stored_at=event['ts'], status='Recorded', source=event.get('rule'))
        if ending:
            item['status'] = 'Ended'
        elif lesson.get('expires_at') and dt.datetime.fromisoformat(lesson['expires_at'].replace('Z', '+00:00')) <= now:
            item['status'] = 'Expired'
        item['conditions'] = {key: lesson[key] for key in ('app', 'target', 'reason', 'applies_when', 'ends_when', 'expires_at') if lesson.get(key) is not None}
        if ending:
            item['conditions']['ended'] = ending['evidence']
        item['evidence'].append(dict(text=event['david'], label='Exact correction', path=path))
        item['interpretations'].append(dict(text=lesson['interpretation'], path=path, label='Saved interpretation'))
        item['locations'].append(location(path, json.dumps(event, ensure_ascii=False, indent=2), recorded_at=event['ts']))
        if ending:
            item['locations'].append(location(path, json.dumps(ending, ensure_ascii=False, indent=2), recorded_at=ending['ts']))
        targets = qa.get(event.get('rule'), [])
        if len(targets) == 1:
            # Different structured lessons keep their own scope, expiry, and ending evidence.
            target = targets[0]
            target['related'].append(dict(id=item['id'], title=item['title']))
            item['related'].append(dict(id=target['id'], title=target['title']))
        records.append(item)


def read(root, now=None):
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError('Profile root does not exist')
    records, warnings = [], []
    qa = read_markdown(root, records, warnings)
    read_journals(root, records, qa, warnings, now or dt.datetime.now(dt.timezone.utc))
    return dict(records=records, warnings=warnings)
