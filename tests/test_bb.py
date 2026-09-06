import unittest
from unittest.mock import patch

from director import bb_app

NOW = 1_800_000_000


def thread(name="a", **fields):
    return {"id": name, "status": "idle", "projectId": "p", "environmentHostId": "host",
            "updatedAt": NOW * 1000, **fields}


def event(kind, seq=1, at=None, **data):
    return {"id": str(seq), "seq": seq, "type": kind, "createdAt": at or NOW * 1000, "data": data}


class ScanTests(unittest.TestCase):
    def test_each_meaningful_event_changes_state(self):
        cases = [event("client/turn/requested", initiator="user"), event("system/manager/user_message", text="continue"),
                 event("provider/error", message="offline"), event("system/error", detail="offline"),
                 event("interaction/request", interactionId="i"),
                 event("system/interaction/lifecycle", interactionId="i"),
                 event("system/permissionGrant/lifecycle", interactionId="i"),
                 event("system/userQuestion/lifecycle", interactionId="i"),
                 event("turn/started"), event("turn/completed"),
                 event("item/completed", item={"type": "agentMessage", "text": "done"}),
                 event("item/completed", item={"type": "userMessage", "text": "continue"})]
        before = bb_app.state(thread(), [])
        for change in cases:
            with self.subTest(kind=change["type"]):
                self.assertNotEqual(before, bb_app.state(thread(), [change]))

    def test_same_message_text_with_new_event_is_a_change(self):
        data = {"item": {"type": "agentMessage", "text": "same question?"}}
        one = event("item/completed", seq=1, **data)
        two = event("item/completed", seq=2, **data)
        self.assertNotEqual(bb_app.state(thread(), [one]), bb_app.state(thread(), [one, two]))

    def test_log_noise_and_sidebar_updates_do_not_reset_review(self):
        noise = [event("thread/contextWindowUsage/updated", tokens=200),
                 event("thread/tokenUsage/updated", tokens=300),
                 event("item/commandExecution/outputDelta", delta="progress"),
                 event("item/completed", item={"type": "reasoning", "text": "thinking"})]
        self.assertEqual(bb_app.state(thread(), []), bb_app.state(thread(updatedAt=0, lastReadAt=5), noise))

    def test_status_and_pending_interaction_changes_are_detected(self):
        before = bb_app.state(thread(), [])
        self.assertNotEqual(before, bb_app.state(thread(status="error"), []))
        self.assertNotEqual(before, bb_app.state(thread(hasPendingInteraction=True), []))

    @patch("director.bb_app.bb", return_value=[])
    def test_exclusions_and_old_threads(self, bb):
        rows = [thread("self"), thread("active", status="active"), thread("starting", status="starting"),
                thread("stopping", status="stopping"), thread("hidden", visibility="hidden"),
                thread("archived", archivedAt=1), thread("deleted", deletedAt=1),
                thread("remote", environmentHostId="other"), thread("old", updatedAt=0), thread("idle")]
        candidates, skipped, errors = bb_app.collect(rows, NOW, "self", "host")
        self.assertEqual({r["id"] for r in candidates}, {"old", "idle"})
        self.assertEqual(skipped + errors, [])
        candidates, _, _ = bb_app.collect(rows, NOW, "self", "host", hours=3)
        self.assertEqual([r["id"] for r in candidates], ["idle"])

    def test_user_grace_boundary_without_rounding(self):
        for age, excluded in [(179.99, True), (180, False), (180.01, False)]:
            events = [event("client/turn/requested", at=(NOW - age) * 1000, initiator="user")]
            with self.subTest(age=age), patch("director.bb_app.bb", return_value=events):
                rows, skipped, errors = bb_app.collect([thread()], NOW, "self", "host")
                self.assertEqual(bool(skipped), excluded)
                self.assertEqual(bool(rows), not excluded)
                self.assertEqual(errors, [])

    def test_agent_messages_do_not_count_as_user_typing(self):
        for data in ({"initiator": "system"}, {"initiator": "agent"},
                     {"initiator": "user", "senderThreadId": "director"}):
            with self.subTest(data=data), patch("director.bb_app.bb", return_value=[event("client/turn/requested", **data)]):
                rows, skipped, _ = bb_app.collect([thread()], NOW, "self", "host")
                self.assertEqual(len(rows), 1)
                self.assertEqual(skipped, [])

    def test_failed_log_is_visible_and_other_threads_are_scanned(self):
        def bb(*args):
            if args[2] == "failed":
                raise RuntimeError("failed")
            return []
        with patch("director.bb_app.bb", side_effect=bb):
            rows, _, errors = bb_app.collect([thread("failed"), thread("healthy")], NOW, "self", "host")
        self.assertEqual([r["id"] for r in rows], ["healthy"])
        self.assertEqual(errors, [{"id": "failed", "error": "RuntimeError", "detail": "failed"}])


if __name__ == "__main__":
    unittest.main()
