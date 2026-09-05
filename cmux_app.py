"""cmux adapter. Reads cmux only: saved agent sessions, live workspaces, recent prompts, and each stopped agent's screen.

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


def workspaces(payload):
    """Live workspaces as {UUID: title}. Accepts a bare list or a {"workspaces": [...]} wrapper."""
    items = payload.get("workspaces") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise ValueError("cmux list-workspaces did not return workspaces")
    result = {}
    for item in items:
        uuid = item.get("workspace_id") or item.get("uuid") or item.get("id")
        if uuid:
            result[str(uuid).upper()] = item.get("title") or item.get("name") or ""
    return result


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
    value = [session["agent_lifecycle"], session["session_id"], session.get("updated_at_unix"), text]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def row(session, title, now, user_times, read):
    surface = session["surface_id"].upper()
    updated = session.get("updated_at_unix") or 0
    user_at = user_times.get(surface)
    text = read(surface)
    tail = tail_text(text)
    return {
        "id": surface, "title": f"{title} · {session.get('agent_display_name') or session['agent']}".strip(" ·"),
        "project": session.get("cwd"), "provider": session["agent"], "status": session["agent_lifecycle"],
        "workspace": session["workspace_id"].upper(), "session": session["session_id"], "pid": session.get("pid"),
        "idle_min": round((now - updated) / 60),
        "user_at_ms": round(user_at * 1000) if user_at else None,
        "user_min_ago": round((now - user_at) / 60, 2) if user_at else None,
        "pending_interaction": session["agent_lifecycle"] == "needsInput", "parent": None,
        "recent_error": None, "ends_with_question": tail.endswith("?"), "last_agent_msg": tail,
        "state": state(session, text),
    }


def collect(sessions, live, self_surface, now, user_times, hours=None, read=screen):
    """Stopped agents on live surfaces. `live` maps workspace UUID to title; `read` fetches a surface's screen."""
    dropped = {"inactive": 0, "self": 0, "closed": 0, "gone": 0, "running": 0, "old": 0}
    latest = {}
    for session in sessions:
        surface = str(session.get("surface_id") or "").upper()
        if not surface or not session.get("active_for_surface"):
            dropped["inactive"] += 1
        elif surface == (self_surface or "").upper():
            dropped["self"] += 1
        elif str(session.get("workspace_id") or "").upper() not in live:
            dropped["closed"] += 1
        elif session.get("stored_pid_exists") is False:
            dropped["gone"] += 1
        elif session.get("agent_lifecycle") in RUNNING:
            dropped["running"] += 1
        elif hours is not None and now - (session.get("updated_at_unix") or 0) > hours * 3600:
            dropped["old"] += 1
        elif (session.get("updated_at_unix") or 0) >= (latest.get(surface, {}).get("updated_at_unix") or 0):
            latest[surface] = session
    candidates, skipped, errors = [], [], []
    for surface, session in latest.items():
        try:
            entry = row(session, live[session["workspace_id"].upper()], now, user_times, read)
            recent = entry["user_at_ms"] is not None and now * 1000 - entry["user_at_ms"] < 180_000
            (skipped if recent else candidates).append(entry)
        except ERRORS as error:
            errors.append({"id": surface, "error": type(error).__name__, "detail": str(error)})
    return candidates, [entry["id"] for entry in skipped], errors, dropped


def scan(self_surface, now, hours=None):
    saved = cmux("sessions", "list", "--all")
    live = workspaces(cmux("list-workspaces", "--id-format", "both"))
    user_times = user_input_times(pathlib.Path(saved["state_dir"]) / "events.jsonl")
    candidates, skipped, errors, dropped = collect(saved["sessions"], live, self_surface, now, user_times, hours)
    return candidates, skipped, errors, {"workspaces": len(live), "listed": len(saved["sessions"]), "dropped": dropped}
