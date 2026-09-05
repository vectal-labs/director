import json
import unittest
from unittest.mock import patch

import cmux_app

NOW = 1_800_000_000
SCREEN = "╭──────╮\n│ Should I use Postgres or SQLite?  │\n╰──────╯\n❯ \n"


def session(surface="SURF-A", **fields):
    return {"agent": "codex", "agent_display_name": "Codex", "agent_lifecycle": "idle", "session_id": "sess-" + surface,
            "surface_id": surface, "workspace_id": "WS-1", "cwd": "/repo", "pid": 1, "stored_pid_exists": True,
            "active_for_surface": True, "updated_at_unix": NOW - 600, **fields}


def event(name="agent.hook.UserPromptSubmit", surface="SURF-A", at=NOW):
    when = cmux_app.dt.datetime.fromtimestamp(at, cmux_app.dt.timezone.utc).isoformat().replace("+00:00", "Z")
    return json.dumps({"name": name, "surface_id": surface, "occurred_at": when})


def collect(sessions, user_times=None, read=lambda surface: SCREEN, **kw):
    live = {s["surface_id"].upper(): {"workspace": "WS-1", "title": "director demo"}
            for s in sessions if s.get("workspace_id") == "WS-1"}
    return cmux_app.collect(sessions, live, "SELF", NOW, user_times or {}, read=read, **kw)


class CmuxTests(unittest.TestCase):
    def test_tree_tracks_open_terminals_and_rejects_incomplete_topology(self):
        workspace = {"workspace_id": "ws-1", "title": "demo", "panes": [{"surfaces": [
            {"uuid": "surf-a", "type": "terminal"}, {"id": "browser", "type": "browser"}]}]}
        payload = {"windows": [{"workspaces": [workspace]}]}
        self.assertEqual(cmux_app.terminals(payload),
                         ({"SURF-A": {"workspace": "WS-1", "title": "demo"}}, 1))
        self.assertEqual(cmux_app.terminals({"windows": []}), ({}, 0))
        for invalid in ({}, {"windows": [None]}, {"windows": [{"workspaces": [{"id": "WS-1"}]}]}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                cmux_app.terminals(invalid)

    def test_screen_tail_drops_chrome_and_detects_question(self):
        tail = cmux_app.tail_text(SCREEN)
        self.assertEqual(tail, "Should I use Postgres or SQLite?")
        self.assertTrue(tail.endswith("?"))
        self.assertEqual(cmux_app.tail_text("› \n$ \n\n"), "")
        # Footers stay: they are the agent's chrome, not a question. The hint is only a ranking tiebreak.
        self.assertFalse(cmux_app.tail_text(SCREEN + "? for shortcuts\n").endswith("?"))

    def test_only_stopped_agents_on_live_surfaces_are_candidates(self):
        sessions = [session("SELF"), session("RUN", agent_lifecycle="running"), session("CLOSED", workspace_id="WS-9"),
                    session("GONE", stored_pid_exists=False), session("STALE", active_for_surface=False),
                    session("NEEDS", agent_lifecycle="needsInput"), session("UNKNOWN", agent_lifecycle="unknown"),
                    session("IDLE")]
        rows, skipped, errors, dropped = collect(sessions)
        self.assertEqual({r["id"] for r in rows}, {"NEEDS", "UNKNOWN", "IDLE", "GONE"})
        self.assertEqual(skipped + errors, [])
        self.assertEqual(dropped, {"inactive": 1, "self": 1, "closed": 1, "running": 1, "old": 0})
        needs = next(r for r in rows if r["id"] == "NEEDS")
        self.assertTrue(needs["pending_interaction"])
        self.assertEqual(needs["title"], "director demo · Codex")
        self.assertEqual(needs["idle_min"], 10)

    def test_hours_filter_and_newest_session_per_surface(self):
        sessions = [session("A", updated_at_unix=NOW - 7200, session_id="older"), session("A", session_id="newer"),
                    session("B", updated_at_unix=NOW - 7200)]
        rows, _, _, dropped = collect(sessions, hours=1)
        self.assertEqual([(r["id"], r["session"]) for r in rows], [("A", "newer")])
        self.assertEqual(dropped["old"], 1)

    def test_old_idle_session_cannot_hide_a_new_running_session(self):
        rows, _, _, dropped = collect([session(session_id="old"),
            session(session_id="new", updated_at_unix=NOW, agent_lifecycle="running")])
        self.assertEqual(rows, [])
        self.assertEqual(dropped["running"], 1)

    def test_recent_user_prompt_skips_the_surface(self):
        for age, excluded in [(179.99, True), (180, False), (180.01, False)]:
            with self.subTest(age=age):
                rows, skipped, errors, _ = collect([session()], {"SURF-A": NOW - age})
                self.assertEqual(bool(skipped), excluded)
                self.assertEqual(bool(rows), not excluded)
                self.assertEqual(errors, [])

    def test_user_prompt_times_come_from_the_event_log(self):
        text = "\n".join(["not json", event(at=NOW - 500), event(name="agent.hook.Stop", at=NOW - 100),
                          event(at=NOW - 300), event(surface="SURF-B", at=NOW - 50), json.dumps([1])])
        with patch("pathlib.Path.read_text", return_value=text):
            times = cmux_app.user_input_times("events.jsonl")
        self.assertEqual(times, {"SURF-A": NOW - 300, "SURF-B": NOW - 50})
        self.assertEqual(cmux_app.user_input_times("/nonexistent/events.jsonl"), {})

    def test_screen_failure_is_visible_and_other_surfaces_are_scanned(self):
        def read(surface):
            if surface == "BROKEN":
                raise RuntimeError("cmux read-screen failed")
            return SCREEN
        rows, _, errors, _ = collect([session("BROKEN"), session("FINE")], read=read)
        self.assertEqual([r["id"] for r in rows], ["FINE"])
        self.assertEqual(errors, [{"id": "BROKEN", "error": "RuntimeError", "detail": "cmux read-screen failed"}])

    def test_state_tracks_lifecycle_session_and_screen(self):
        base = cmux_app.state(session(), SCREEN)
        self.assertEqual(base, cmux_app.state(session(), SCREEN))
        self.assertNotEqual(base, cmux_app.state(session(agent_lifecycle="needsInput"), SCREEN))
        self.assertNotEqual(base, cmux_app.state(session(session_id="new"), SCREEN))
        self.assertNotEqual(base, cmux_app.state(session(), SCREEN + "Done.\n"))
        self.assertNotEqual(base, cmux_app.state(session(stored_pid_exists=False), SCREEN))

    def test_scan_reads_cmux_only(self):
        calls = []

        def fake(*args, as_json=True):
            calls.append(args)
            if args[0] == "sessions":
                return {"state_dir": "/nonexistent", "sessions": [session()]}
            if args[0] == "tree":
                return {"windows": [{"workspaces": [{"id": "ws-1", "title": "director demo", "panes": [
                    {"surfaces": [{"id": "SURF-A", "type": "terminal"}]}]}]}]}
            return SCREEN
        with patch("cmux_app.cmux", side_effect=fake):
            rows, skipped, errors, extras = cmux_app.scan("SELF", NOW)
        self.assertEqual([r["id"] for r in rows], ["SURF-A"])
        self.assertEqual((skipped, errors), ([], []))
        self.assertEqual(extras["workspaces"], 1)
        self.assertEqual(calls, [("sessions", "list", "--all"), ("tree", "--all", "--id-format", "both"),
                                 ("read-screen", "--surface", "SURF-A", "--lines", "60")])


if __name__ == "__main__":
    unittest.main()
