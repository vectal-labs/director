"""cmux adapter. Reads cmux only: saved sessions, open terminals, recent prompts, and stopped agents' screens.

Nothing here types into a surface, focuses anything, or changes cmux state.
"""
import datetime as dt
import hashlib
import json
import os
import pathlib
import shutil
import subprocess

SELF_VAR = "CMUX_SURFACE_ID"
RUNNING = {"running"}
BUNDLED_CLI = "/Applications/cmux.app/Contents/Resources/bin/cmux"
SCREEN_LINES = 60
BOX = " \t─│┌┐└┘├┤╭╮╰╯━┃┏┓┗┛▔▁"
PROMPT_MARKS = ("❯", "›", ">", "$")
ERRORS = (RuntimeError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired)


def cmux_bin():
    return shutil.which("cmux") or (BUNDLED_CLI if os.path.exists(BUNDLED_CLI) else "cmux")


def cmux(*args, as_json=True):
    command = [cmux_bin(), *args] + (["--json"] if as_json else [])
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError(f"cmux {' '.join(args)} failed (exit {result.returncode}): {result.stderr.strip()[:200]}")
    return json.loads(result.stdout) if as_json else result.stdout


def tree_items(node, key):
    items = node.get(key) if isinstance(node, dict) else None
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ValueError(f"cmux tree did not return {key}")
    return items


def tree_id(node, kind):
    value = node.get(f"{kind}_id") or node.get("uuid") or node.get("id")
    if not value:
        raise ValueError(f"cmux tree is missing a {kind} ID")
    return str(value).upper()


def terminals(payload):
    """Map open terminal UUIDs to their current workspace and title across all windows."""
    live, workspaces = {}, set()
    for window in tree_items(payload, "windows"):
        for workspace in tree_items(window, "workspaces"):
            uuid = tree_id(workspace, "workspace")
            workspaces.add(uuid)
            for pane in tree_items(workspace, "panes"):
                for surface in tree_items(pane, "surfaces"):
                    if surface.get("type") == "terminal":
                        live[tree_id(surface, "surface")] = {
                            "workspace": uuid, "title": workspace.get("title") or workspace.get("name") or ""}
    return live, len(workspaces)


def user_input_times(path):
    """Last prompt submitted to each surface, from cmux's event log (agent.hook.UserPromptSubmit)."""
    last = {}
    try:
        lines = pathlib.Path(path).read_text().splitlines()
    except OSError:
        return last
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("name") == "agent.hook.UserPromptSubmit" and event.get("surface_id"):
            when = dt.datetime.fromisoformat(event["occurred_at"].replace("Z", "+00:00")).timestamp()
            last[event["surface_id"].upper()] = max(when, last.get(event["surface_id"].upper(), 0))
    return last


def screen(surface):
    return cmux("read-screen", "--surface", surface, "--lines", str(SCREEN_LINES), as_json=False)


def tail_text(text, limit=400):
    """Agent output without box drawing, input prompts, or blank lines."""
    lines = [line.strip(BOX) for line in text.splitlines()]
    lines = [line for line in lines if line and not line.startswith(PROMPT_MARKS)]
    return "\n".join(lines)[-limit:].strip()


def state(session, text):
    value = [status(session), session["session_id"], session.get("updated_at_unix"), text]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def status(session):
    # A crashed process can leave its hook lifecycle stuck on "running".
    return "exited" if session.get("stored_pid_exists") is False else session["agent_lifecycle"]


def row(session, terminal, now, user_times, read):
    surface = session["surface_id"].upper()
    updated = session.get("updated_at_unix") or 0
    user_at = user_times.get(surface)
    text = read(surface)
    tail = tail_text(text)
    if not session.get("session_id") or not session.get("agent"):
        raise ValueError("cmux session is missing its agent or session ID")
    return {
        "id": surface, "review_key": f"cmux:{session['agent']}:{session['session_id']}",
        "title": f"{terminal['title']} · {session.get('agent_display_name') or session['agent']}".strip(" ·"),
        "project": session.get("cwd"), "provider": session["agent"], "status": status(session),
        "workspace": terminal["workspace"], "session": session["session_id"], "pid": session.get("pid"),
        "idle_min": round((now - updated) / 60),
        "user_at_ms": round(user_at * 1000) if user_at else None,
        "user_min_ago": round((now - user_at) / 60, 2) if user_at else None,
        "pending_interaction": status(session) == "needsInput", "parent": None,
        "recent_error": None, "ends_with_question": tail.endswith("?"), "last_agent_msg": tail,
        "state": state(session, text),
    }


def collect(sessions, live, self_surface, now, user_times, hours=None, read=screen):
    """Stopped agents on open terminals. `live` comes from the all-window tree."""
    dropped = {"inactive": 0, "self": 0, "closed": 0, "running": 0, "old": 0}
    latest = {}
    for session in sessions:
        surface = str(session.get("surface_id") or "").upper()
        if not surface or not session.get("active_for_surface"):
            dropped["inactive"] += 1
        elif surface == (self_surface or "").upper():
            dropped["self"] += 1
        elif surface not in live:
            dropped["closed"] += 1
        elif (session.get("updated_at_unix") or 0) >= (latest.get(surface, {}).get("updated_at_unix") or 0):
            latest[surface] = session
    candidates, skipped, errors = [], [], []
    for surface, session in latest.items():
        try:
            if status(session) in RUNNING:
                dropped["running"] += 1
                continue
            if hours is not None and now - (session.get("updated_at_unix") or 0) > hours * 3600:
                dropped["old"] += 1
                continue
            entry = row(session, live[surface], now, user_times, read)
            recent = entry["user_at_ms"] is not None and now * 1000 - entry["user_at_ms"] < 180_000
            (skipped if recent else candidates).append(entry)
        except ERRORS as error:
            errors.append({"id": surface, "error": type(error).__name__, "detail": str(error)})
    return candidates, [entry["id"] for entry in skipped], errors, dropped


def scan(self_surface, now, hours=None):
    saved = cmux("sessions", "list", "--all")
    live, workspace_count = terminals(cmux("tree", "--all", "--id-format", "both"))
    user_times = user_input_times(pathlib.Path(saved["state_dir"]) / "events.jsonl")
    candidates, skipped, errors, dropped = collect(saved["sessions"], live, self_surface, now, user_times, hours)
    return candidates, skipped, errors, {"workspaces": workspace_count, "listed": len(saved["sessions"]), "dropped": dropped}
