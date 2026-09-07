"""Independent lesson journals, projected from append-only review evidence.

Durable teaching lives in profile; temporary instructions and exceptions stay in
state. Journal rows keep original structured override and lesson_end events, so
teaching retains its exact source without requiring the original review log.
"""
from contextlib import ExitStack, contextmanager
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import tempfile

try:
    from . import memory
    from .storage import ROOT
except ImportError:
    import memory
    from storage import ROOT

DURABLE_KINDS = {"general_preference", "project_decision"}


@contextmanager
def locked(root=ROOT):
    """One lock covers review IDs and both projections, including standalone ends."""
    state = Path(root) / "state"
    state.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        file = stack.enter_context((state / ".memory.lock").open("a"))
        fcntl.flock(file, fcntl.LOCK_EX)
        history = state / "log.jsonl"
        if history.exists():
            # Cooperate with a legacy writer that still holds the migrated log inode.
            log = stack.enter_context(history.open("r"))
            fcntl.flock(log, fcntl.LOCK_EX)
        yield


def partition_events(text):
    """Validate full history, then return unchanged (durable, temporary) events."""
    memory.read(text)
    durable, temporary, targets = [], [], {}
    for line in text.splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if event.get("kind") == "override" and event.get("lesson"):
            destination = durable if event["lesson"]["kind"] in DURABLE_KINDS else temporary
            targets[event["lesson"]["id"]] = destination
            destination.append(event)
        elif event.get("kind") == "lesson_end":
            targets[event["lesson_id"]].append(event)
    return durable, temporary


def _fold(events, durable, source):
    records = {}
    for number, event in enumerate(events, 1):
        try:
            if not isinstance(event, dict) or type(event.get("run")) is not int or event["run"] <= 0:
                raise ValueError("invalid source run")
            dt.datetime.fromisoformat(event["ts"])
            if event.get("kind") == "override":
                lesson = event["lesson"]
                memory.validate_lesson(lesson, bound=True)
                if (lesson["kind"] in DURABLE_KINDS) != durable:
                    raise ValueError("lesson is in the wrong journal")
                if (event["decision"] not in memory.DECISIONS or not isinstance(event.get("david"), str)
                        or not event["david"].strip() or lesson["id"] in records):
                    raise ValueError("invalid or duplicate lesson source")
                records[lesson["id"]] = {"source": event, "ending": None}
            elif event.get("kind") == "lesson_end":
                record = records[event["lesson_id"]]
                if (record["ending"] or event["run"] != record["source"]["run"]
                        or not isinstance(event.get("evidence"), str) or not event["evidence"].strip()):
                    raise ValueError("invalid lesson ending")
                record["ending"] = event
            else:
                raise ValueError("unknown lesson event")
        except (ValueError, KeyError, TypeError) as error:
            raise ValueError(f"{source} line {number}: invalid lesson record ({error})") from error
    return records


def _read(path):
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "", []
    try:
        return text, [json.loads(line) for line in text.splitlines() if line.strip()]
    except ValueError as error:
        raise ValueError(f"{path}: invalid lesson JSON") from error


def load(root=ROOT):
    """Read both journals without opening review history or mutating files."""
    records = {}
    for folder, durable in (("profile", True), ("state", False)):
        path = Path(root) / folder / "lessons.jsonl"
        _, events = _read(path)
        current = _fold(events, durable, path)
        if records.keys() & current.keys():
            raise ValueError("lesson ID occurs in both profile and state journals")
        records.update(current)
    return records


def _append(path, text, events):
    """Replace each small journal atomically; keep all existing bytes untouched."""
    if not events:
        return
    prefix = "\n" if text and not text.endswith("\n") else ""
    contents = text + prefix + "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".lessons-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(contents)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def reconcile(text, root=ROOT, *, write=True):
    """Repair interrupted history projections. Caller holds locked(root).

    Journal-only lessons/endings remain authoritative. Conflicting IDs stop the
    operation before writes; ordinary legacy overrides never become lessons.
    """
    extracted = partition_events(text)
    records = load(root)
    pending = []
    for (folder, durable), history in zip((("profile", True), ("state", False)), extracted):
        path = Path(root) / folder / "lessons.jsonl"
        contents, events = _read(path)
        additions = []
        for event in history:
            ending = event.get("kind") == "lesson_end"
            lesson_id = event["lesson_id"] if ending else event["lesson"]["id"]
            existing = records.get(lesson_id)
            prior = existing["ending" if ending else "source"] if existing else None
            if prior is not None:
                if prior != event:
                    raise ValueError(f"conflicting source for lesson {lesson_id}; preserve both files and resolve it explicitly")
                continue
            additions.append(event)
            if ending:
                records[lesson_id]["ending"] = event
            else:
                records[lesson_id] = {"source": event, "ending": None}
        _fold(events + additions, durable, path)
        pending.append((path, contents, additions))
    if write:
        for path, contents, additions in pending:
            _append(path, contents, additions)
    return records


def records_for_scan(records):
    def source_order(record):
        source = record["source"]
        lesson_id = source["lesson"]["id"]
        suffix = lesson_id.rsplit(".", 1)[-1]
        return (dt.datetime.fromisoformat(source["ts"]).timestamp(), source["run"],
                int(suffix) if suffix.isdigit() else 0, lesson_id)

    return [{**record["source"]["lesson"], "david": record["source"]["david"],
             "run": record["source"]["run"], "rule": record["source"].get("rule"),
             "recorded_at": record["source"]["ts"], "lesson_ended": record["ending"]}
            for record in sorted(records.values(), key=source_order)]


def end(event, records, root=ROOT):
    """End a journal lesson even when its original review history is absent."""
    record = records.get(event["lesson_id"])
    if record is None:
        raise ValueError(f"no lesson {event['lesson_id']}")
    durable = record["source"]["lesson"]["kind"] in DURABLE_KINDS
    path = Path(root) / ("profile" if durable else "state") / "lessons.jsonl"
    text, events = _read(path)
    _fold(events + [event], durable, path)
    _append(path, text, [event])
