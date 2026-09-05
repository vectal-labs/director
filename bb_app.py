"""bb adapter. Reads bb only: every stopped thread on this Mac, its recent input, and a state fingerprint.

Nothing here messages an agent or resolves an interaction.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import subprocess

SELF_VAR = "BB_THREAD_ID"
RUNNING = {"active", "starting", "stopping"}
MEANINGFUL = {
    "client/turn/requested", "client/turn/rejected", "system/manager/user_message", "provider/error", "system/error",
    "turn/started", "turn/completed", "system/thread/interrupted",
    "interaction/request", "system/interaction/lifecycle", "system/permissionGrant/lifecycle",
    "system/userQuestion/lifecycle", "thread/status/changed",
}
ERRORS = (RuntimeError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired)


def bb(*args):
    result = subprocess.run(["bb", *args, "--json"], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError(f"bb {' '.join(args)} failed (exit {result.returncode})")
    return json.loads(result.stdout)


def last_text(events, item_type):
    for event in reversed(events):
        item = event.get("data", {}).get("item", {})
        if event["type"] == "item/completed" and item.get("type") == item_type:
            return item.get("text", "")
    return ""


def state(thread, events):
    relevant = []
    for event in events:
        item = event.get("data", {}).get("item", {})
        message = event["type"] == "item/completed" and item.get("type") in {"agentMessage", "userMessage"}
        if event["type"] in MEANINGFUL or message:
            relevant.append((event.get("id"), event.get("seq"), event["createdAt"], event["type"], event["data"]))
    value = [thread["status"], thread.get("hasPendingInteraction", False), relevant]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def scan_thread(thread, now):
    events = bb("thread", "log", thread["id"], "--all")
    if not isinstance(events, list):
        raise ValueError("bb thread log did not return events")
    users = [e for e in events if e["type"] == "client/turn/requested"
             and e["data"].get("initiator") not in {"system", "agent"} and not e["data"].get("senderThreadId")]
    last_user_at = users[-1]["createdAt"] if users else None
    errors = [e for e in events if e["type"] in {"provider/error", "system/error"}]
    error = errors[-1] if errors and errors[-1]["createdAt"] >= events[-1]["createdAt"] - 60_000 else None
    agent = last_text(events, "agentMessage")
    return {
        "id": thread["id"], "title": thread.get("title") or thread.get("titleFallback", "")[:80],
        "project": thread["projectId"], "provider": thread.get("providerId"), "status": thread["status"],
        "idle_min": round((now * 1000 - thread["updatedAt"]) / 60_000),
        "user_at_ms": last_user_at,
        "user_min_ago": round((now * 1000 - last_user_at) / 60_000, 2) if last_user_at is not None else None,
        "pending_interaction": thread.get("hasPendingInteraction", False), "parent": thread.get("parentThreadId"),
        "recent_error": str(error["data"].get("detail") or error["data"].get("message") or "error")[:160] if error else None,
        "ends_with_question": agent.rstrip().endswith("?"), "last_agent_msg": agent[-400:].strip(),
        "state": state(thread, events),
    }


def collect(threads, now, self_id, host_id, hours=None):
    eligible = [t for t in threads if t["id"] != self_id and not t.get("archivedAt")
                and not t.get("deletedAt") and t.get("visibility") != "hidden" and t["status"] not in RUNNING
                and t.get("environmentHostId") == host_id
                and (hours is None or now * 1000 - t["updatedAt"] <= hours * 3600_000)]
    candidates, skipped, errors = [], [], []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [(thread, pool.submit(scan_thread, thread, now)) for thread in eligible]
        for thread, future in futures:
            try:
                row = future.result()
                recent = row["user_at_ms"] is not None and now * 1000 - row["user_at_ms"] < 180_000
                (skipped if recent else candidates).append(row)
            except ERRORS as error:
                errors.append({"id": thread["id"], "error": type(error).__name__, "detail": str(error)})
    return candidates, [row["id"] for row in skipped], errors


def scan(self_id, now, hours=None):
    host = bb("status")["thread"]["environment"]["hostId"]
    threads = bb("thread", "list")
    candidates, skipped, errors = collect(threads, now, self_id, host, hours)
    return candidates, skipped, errors, {"host": host, "listed": len(threads)}
