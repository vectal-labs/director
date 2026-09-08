"""cmux adapter. Reads cmux only: saved sessions, open terminals, recent prompts, and stopped agents' screens.

Nothing here types into a surface, focuses anything, or changes cmux state.
"""
from dataclasses import dataclass, field
import datetime as dt
import hashlib
import json
import math
import os
import pathlib
import re
import shutil
import subprocess

SELF_VAR = "CMUX_SURFACE_ID"
RUNNING = {"running"}
BUNDLED_CLI = "/Applications/cmux.app/Contents/Resources/bin/cmux"
SCREEN_LINES = 60
BOX = " \t─│┌┐└┘├┤╭╮╰╯━┃┏┓┗┛▔▁"
PROMPT_MARKS = ("❯", "›", ">", "$")
ERRORS = (RuntimeError, ValueError, KeyError, TypeError, OSError, OverflowError, subprocess.TimeoutExpired)


def cmux_bin():
    return shutil.which("cmux") or (BUNDLED_CLI if os.path.exists(BUNDLED_CLI) else "cmux")


def cmux(*args, as_json=True):
    command = [cmux_bin(), *args] + (["--json"] if as_json else [])
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError(f"cmux {' '.join(args)} failed (exit {result.returncode})")
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


@dataclass
class InputHistory:
    times: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)
    unknown_surfaces: set = field(default_factory=set)
    all_unknown: bool = False

    def known(self, surface):
        return not self.all_unknown and surface.upper() not in self.unknown_surfaces


def user_input_times(path):
    """Keep valid prompt times; report gaps without treating them as absence of input."""
    history = InputHistory()
    try:
        lines = pathlib.Path(path).read_bytes().splitlines()
    except OSError as error:
        history.all_unknown = True
        history.errors.append({"id": None, "error": type(error).__name__,
                               "detail": "events.jsonl: input history could not be read"})
        return history
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        surface = None
        try:
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError("invalid event")
            value = event.get("surface_id")
            if isinstance(value, str) and value.strip():
                surface = value.upper()
            if not isinstance(event.get("name"), str) or not event["name"]:
                raise ValueError("invalid event name")
            if event["name"] != "agent.hook.UserPromptSubmit":
                continue
            when = event.get("occurred_at")
            if surface is None or not isinstance(when, str):
                raise ValueError("invalid prompt event")
            # fromisoformat normalizes offsets such as +00:99, so check the zone first.
            if not re.search(r"(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])$", when):
                raise ValueError("invalid prompt timezone")
            parsed = dt.datetime.fromisoformat(when.replace("Z", "+00:00"))
            if parsed.utcoffset() is None:
                raise ValueError("missing prompt timezone")
            at = parsed.timestamp()
            history.times[surface] = max(at, history.times.get(surface, at))
        except (ValueError, OverflowError, OSError) as error:
            history.errors.append({"id": surface, "error": type(error).__name__,
                                   "detail": f"events.jsonl line {number}: invalid input history event"})
            if surface is None:
                history.all_unknown = True
            else:
                history.unknown_surfaces.add(surface)
    return history


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


def row(session, terminal, now, read):
    surface = session["surface_id"].upper()
    updated = session.get("updated_at_unix") or 0
    text = read(surface)
    tail = tail_text(text)
    return {
        "id": surface, "review_key": f"cmux:{session['agent']}:{session['session_id']}",
        "title": f"{terminal['title']} · {session.get('agent_display_name') or session['agent']}".strip(" ·"),
        "project": session.get("cwd"), "provider": session["agent"], "status": status(session),
        "workspace": terminal["workspace"], "session": session["session_id"], "pid": session.get("pid"),
        "idle_min": round((now - updated) / 60),
        "pending_interaction": status(session) == "needsInput", "parent": None,
        "recent_error": None, "ends_with_question": tail.endswith("?"), "last_agent_msg": tail,
        "state": state(session, text),
    }


def collect(sessions, live, self_surface, now, read_history, hours=None, read=screen, recent_user_seconds=180):
    """Stopped agents on open terminals. `live` comes from the all-window tree."""
    if not isinstance(sessions, list):
        raise ValueError("cmux sessions list did not return sessions")
    dropped = {"inactive": 0, "self": 0, "closed": 0, "running": 0, "old": 0}
    latest, invalid = {}, set()
    rows, candidates, skipped, errors = [], [], [], []
    for session in sessions:
        surface = None
        try:
            if not isinstance(session, dict):
                raise ValueError("cmux session must be an object")
            if not isinstance(session.get("surface_id"), str) or not session["surface_id"].strip():
                raise ValueError("cmux session is missing a valid surface ID")
            surface = session["surface_id"].upper()
            if not isinstance(session.get("active_for_surface"), bool):
                raise ValueError("cmux session has an invalid active_for_surface")
            if not session["active_for_surface"]:
                dropped["inactive"] += 1
                continue
            if surface == (self_surface or "").upper():
                dropped["self"] += 1
                continue
            if surface not in live:
                dropped["closed"] += 1
                continue
            updated = session.get("updated_at_unix")
            if type(updated) not in (int, float) or not math.isfinite(updated):
                raise ValueError("cmux session has an invalid updated_at_unix")
            for key in ("agent", "session_id", "agent_lifecycle"):
                if not isinstance(session.get(key), str) or not session[key]:
                    raise ValueError(f"cmux session is missing a valid {key}")
            if updated >= latest.get(surface, {}).get("updated_at_unix", 0):
                latest[surface] = session
        except ERRORS as error:
            errors.append({"id": surface, "error": type(error).__name__, "detail": str(error)})
            if surface is not None:
                invalid.add(surface)
    for surface, session in latest.items():
        if surface in invalid:
            continue  # A malformed newer session must not expose stale state from this surface.
        try:
            if status(session) in RUNNING:
                dropped["running"] += 1
                continue
            if hours is not None and now - session["updated_at_unix"] > hours * 3600:
                dropped["old"] += 1
                continue
            rows.append(row(session, live[surface], now, read))
        except ERRORS as error:
            errors.append({"id": surface, "error": type(error).__name__, "detail": str(error)})
    # Read after screens, which can be slow, so input submitted during collection is seen.
    history = read_history()
    errors.extend(history.errors)
    for entry in rows:
        user_at = history.times.get(entry["id"])
        entry.update(input_history_known=history.known(entry["id"]),
                     user_at_ms=user_at * 1000 if user_at is not None else None,
                     user_min_ago=round((now - user_at) / 60, 2) if user_at is not None else None)
        recent = entry["user_at_ms"] is not None and now * 1000 - entry["user_at_ms"] < recent_user_seconds * 1000
        (skipped if recent else candidates).append(entry)
    return candidates, [entry["id"] for entry in skipped], errors, dropped


def scan(self_surface, now, hours=None, recent_user_seconds=180):
    saved = cmux("sessions", "list", "--all")
    if not isinstance(saved, dict) or not isinstance(saved.get("sessions"), list):
        raise ValueError("cmux sessions list did not return sessions")
    if not isinstance(saved.get("state_dir"), str) or not saved["state_dir"]:
        raise ValueError("cmux sessions list did not return a state directory")
    live, workspace_count = terminals(cmux("tree", "--all", "--id-format", "both"))
    history_path = pathlib.Path(saved["state_dir"]) / "events.jsonl"
    candidates, skipped, errors, dropped = collect(
        saved["sessions"], live, self_surface, now, lambda: user_input_times(history_path), hours, recent_user_seconds=recent_user_seconds)
    return candidates, skipped, errors, {"workspaces": workspace_count, "listed": len(saved["sessions"]), "dropped": dropped}
