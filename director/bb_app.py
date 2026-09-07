"""bb adapter. Reads bb only: every stopped thread on this Mac, its recent input, and a state fingerprint.

Nothing here messages an agent or resolves an interaction.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import subprocess

SELF_VAR = "BB_THREAD_ID"
RUNNING = {"active", "starting", "stopping"}
MEANINGFUL = {
    "client/turn/requested", "client/turn/rejected", "system/manager/user_message", "provider/error", "system/error",
    "turn/started", "turn/completed", "system/thread/interrupted",
    "interaction/request", "system/interaction/lifecycle", "system/permissionGrant/lifecycle",
    "system/userQuestion/lifecycle", "thread/status/changed",
}
ERRORS = (RuntimeError, ValueError, KeyError, TypeError, OSError, OverflowError, subprocess.TimeoutExpired)


def bb(*args):
    result = subprocess.run(["bb", *args, "--json"], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError(f"bb {' '.join(args)} failed (exit {result.returncode})")
    return json.loads(result.stdout)


def last_text(events, item_type):
    for event in reversed(events):
        if event["type"] == "item/completed":
            item = event["data"]["item"]
            if item["type"] == item_type:
                return item["text"]
    return ""


def state(thread, events):
    relevant = []
    for event in events:
        message = (event["type"] == "item/completed"
                   and event["data"]["item"]["type"] in {"agentMessage", "userMessage"})
        if event["type"] in MEANINGFUL or message:
            relevant.append((event.get("id"), event.get("seq"), event["createdAt"], event["type"], event["data"]))
    value = [thread["status"], thread.get("hasPendingInteraction", False), relevant]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def validate_events(events):
    if not isinstance(events, list):
        raise ValueError("bb thread log did not return events")
    for number, event in enumerate(events, 1):
        detail = f"bb thread log event {number} is malformed"
        if not isinstance(event, dict) or not isinstance(event.get("type"), str) or not event["type"]:
            raise ValueError(detail)
        at = event.get("createdAt")
        if type(at) not in (int, float) or not math.isfinite(at):
            raise ValueError(detail + ": invalid createdAt")
        if event["type"] not in MEANINGFUL and event["type"] != "item/completed":
            continue
        data = event.get("data")
        if not isinstance(data, dict):
            raise ValueError(detail + ": invalid data")
        if event["type"] == "client/turn/requested":
            if any(data.get(key) is not None and not isinstance(data[key], str)
                   for key in ("initiator", "senderThreadId")):
                raise ValueError(detail + ": invalid sender")
        if event["type"] == "item/completed":
            item = data.get("item")
            if not isinstance(item, dict) or not isinstance(item.get("type"), str):
                raise ValueError(detail + ": invalid item")
            if item["type"] in {"agentMessage", "userMessage"} and not isinstance(item.get("text"), str):
                raise ValueError(detail + ": invalid message text")


def scan_thread(thread, now):
    events = bb("thread", "log", thread["id"], "--all")
    validate_events(events)
    users = [e for e in events if e["type"] == "client/turn/requested"
             and e["data"].get("initiator") not in {"system", "agent"} and not e["data"].get("senderThreadId")]
    last_user_at = max((event["createdAt"] for event in users), default=None)
    errors = [e for e in events if e["type"] in {"provider/error", "system/error"}]
    error = max(errors, key=lambda event: event["createdAt"], default=None)
    if error and error["createdAt"] < max(event["createdAt"] for event in events) - 60_000:
        error = None
    agent = last_text(events, "agentMessage")
    return {
        "id": thread["id"], "title": thread.get("title") or (thread.get("titleFallback") or "")[:80],
        "project": thread["projectId"], "provider": thread.get("providerId"), "status": thread["status"],
        "idle_min": round((now * 1000 - thread["updatedAt"]) / 60_000),
        "input_history_known": True, "user_at_ms": last_user_at,
        "user_min_ago": round((now * 1000 - last_user_at) / 60_000, 2) if last_user_at is not None else None,
        "pending_interaction": thread.get("hasPendingInteraction", False), "parent": thread.get("parentThreadId"),
        "recent_error": str(error["data"].get("detail") or error["data"].get("message") or "error")[:160] if error else None,
        "ends_with_question": agent.rstrip().endswith("?"), "last_agent_msg": agent[-400:].strip(),
        "state": state(thread, events),
    }


def collect(threads, now, self_id, host_id, hours=None, recent_user_seconds=180):
    if not isinstance(threads, list):
        raise ValueError("bb thread list did not return threads")
    eligible, candidates, skipped, errors = [], [], [], []
    for thread in threads:
        thread_id = None
        try:
            if not isinstance(thread, dict) or not isinstance(thread.get("id"), str) or not thread["id"]:
                raise ValueError("bb thread is missing a valid ID")
            thread_id = thread["id"]
            if thread_id == self_id or thread.get("archivedAt") or thread.get("deletedAt"):
                continue
            if thread.get("visibility") == "hidden":
                continue
            for key in ("status", "environmentHostId", "projectId"):
                if not isinstance(thread.get(key), str) or not thread[key]:
                    raise ValueError(f"bb thread is missing a valid {key}")
            if thread["status"] in RUNNING or thread["environmentHostId"] != host_id:
                continue
            updated = thread.get("updatedAt")
            if type(updated) not in (int, float) or not math.isfinite(updated):
                raise ValueError("bb thread has an invalid updatedAt")
            for key in ("title", "titleFallback"):
                if thread.get(key) is not None and not isinstance(thread[key], str):
                    raise ValueError(f"bb thread has an invalid {key}")
            if hours is None or now * 1000 - updated <= hours * 3600_000:
                eligible.append(thread)
        except ERRORS as error:
            errors.append({"id": thread_id, "error": type(error).__name__, "detail": str(error)})
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [(thread, pool.submit(scan_thread, thread, now)) for thread in eligible]
        for thread, future in futures:
            try:
                row = future.result()
                recent = row["user_at_ms"] is not None and now * 1000 - row["user_at_ms"] < recent_user_seconds * 1000
                (skipped if recent else candidates).append(row)
            except ERRORS as error:
                errors.append({"id": thread["id"], "error": type(error).__name__, "detail": str(error)})
    return candidates, [row["id"] for row in skipped], errors


def scan(self_id, now, hours=None, recent_user_seconds=180):
    status = bb("status")
    thread = status.get("thread") if isinstance(status, dict) else None
    environment = thread.get("environment") if isinstance(thread, dict) else None
    host = environment.get("hostId") if isinstance(environment, dict) else None
    if not isinstance(host, str) or not host:
        raise ValueError("bb status did not return an environment host ID")
    threads = bb("thread", "list")
    candidates, skipped, errors = collect(threads, now, self_id, host, hours, recent_user_seconds)
    return candidates, skipped, errors, {"host": host, "listed": len(threads)}


def learning_sessions(self_id, project=None, session=None):
    """Inventory for observation, independent of intervention eligibility."""
    host = bb('status')['thread']['environment']['hostId']
    if not isinstance(host, str) or not host:
        raise ValueError('bb status did not return an environment host ID')
    with ThreadPoolExecutor(max_workers=2) as pool:
        current = pool.submit(bb, 'thread', 'list')
        archived = pool.submit(bb, 'thread', 'list', '--archived')
        groups = [current.result(), archived.result()]
    rows = {}
    for group in groups:
        if not isinstance(group, list):
            raise ValueError('bb thread list did not return threads')
        for thread in group:
            if (thread['id'] == self_id or thread.get('deletedAt')
                    or thread.get('visibility') == 'hidden' or thread.get('environmentHostId') != host):
                continue
            if project and thread.get('projectId') != project:
                continue
            if session and thread['id'] != session:
                continue
            rows[thread['id']] = {
                'id': thread['id'], 'title': thread.get('title') or thread.get('titleFallback') or '',
                'project': thread.get('projectId'), 'status': thread['status'],
                'updated': thread['updatedAt'], 'archived': bool(thread.get('archivedAt')),
            }
    return list(rows.values())


def learning_context(session, limit):
    events = bb('thread', 'log', session['id'], '--all')
    validate_events(events)
    messages, seen = [], set()
    for event in events:
        role, text, non_text = None, '', False
        if event['type'] not in {'client/turn/requested', 'item/completed'}:
            continue
        data = event['data']
        if event['type'] == 'client/turn/requested':
            initiator = data.get('initiator')
            if data.get('senderThreadId') or initiator == 'agent':
                role = 'agent_input'
            elif initiator == 'system' or data.get('systemMessageKind') not in {None, 'unlabeled'}:
                role = 'system'
            elif initiator == 'user':
                role = 'human'
            else:
                role = 'unknown'
            inputs = data.get('input')
            if not isinstance(inputs, list):
                raise ValueError('bb prompt is missing input')
            if any(not isinstance(item, dict) for item in inputs):
                raise ValueError('bb prompt input is malformed')
            texts = [item.get('text') for item in inputs if item.get('type') == 'text']
            if any(not isinstance(value, str) for value in texts):
                raise ValueError('bb prompt text is malformed')
            text = '\n'.join(texts)
            non_text = any(item.get('type') != 'text' for item in inputs)
        elif event['type'] == 'item/completed' and data['item']['type'] == 'agentMessage':
            role, text = 'assistant', data['item']['text']
        if role is None:
            continue
        identity = event.get('id') or event.get('seq')
        if identity is None:
            raise ValueError('bb conversation event is missing a stable identity')
        source = f"bb:{session['id']}:{identity}"
        if source in seen:
            continue
        seen.add(source)
        messages.append({'source': source, 'role': role, 'text': text[:6000],
                         'at': event['createdAt'], 'seq': event.get('seq'),
                         'truncated': len(text) > 6000, 'non_text_input': non_text})
    return {**session, 'messages': messages[-limit:], 'message_count': len(messages),
            'truncated': len(messages) > limit or any(m['truncated'] for m in messages[-limit:])}
