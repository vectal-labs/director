import copy
import datetime as dt
import json
import unittest
from unittest.mock import patch

import memory

NOW = 1_800_000_000


def candidate(name="a", **fields):
    return {"id": name, "state": "same", "pending_interaction": False,
            "recent_error": None, "ends_with_question": False, "idle_min": 1, **fields}


def review(name="a", age=10, **fields):
    return {"run": 1, "picked": name, "ts": dt.datetime.fromtimestamp(NOW - age, dt.timezone.utc).isoformat(),
            "decision": "leave", "reviewed_state": "same", **fields}


class MemoryTests(unittest.TestCase):
    def test_leave_returns_at_exactly_one_hour(self):
        for age, eligible in [(3599.999, False), (3600, True), (3601, True)]:
            with self.subTest(age=age):
                rows = [candidate()]
                memory.rank(rows, [review(age=age)], NOW, 1)
                self.assertEqual(rows[0]["review_eligible"], eligible)

    def test_changed_thread_returns_immediately(self):
        rows = [candidate(state="new")]
        memory.rank(rows, [review()], NOW, 1)
        self.assertEqual(rows[0]["eligibility_reason"], "state_changed")
        self.assertTrue(rows[0]["review_eligible"])

    def test_recent_leave_sinks_even_with_pending_interaction(self):
        rows = [candidate(pending_interaction=True), candidate("b")]
        selection = memory.rank(rows, [review()], NOW, 1)
        self.assertEqual([r["id"] for r in rows], ["b", "a"])
        self.assertEqual(selection["suggested"], "b")
        self.assertFalse(rows[1]["spot_check"])

    def test_nothing_to_review(self):
        for rows in ([], [candidate()]):
            selection = memory.rank(rows, [review()], NOW, 1)
            self.assertEqual(selection["mode"], "none")
            self.assertIsNone(selection["suggested"])

    def test_non_leave_decisions_and_missing_snapshots_remain_reviewable(self):
        for fields in ({"decision": "unblock"}, {"decision": "wait_for_david"},
                       {"decision": "deny"}, {"reviewed_state": None}):
            with self.subTest(fields=fields):
                rows = [candidate()]
                memory.rank(rows, [review(**fields)], NOW, 1)
                self.assertTrue(rows[0]["review_eligible"])

    def test_legacy_and_append_only_corrections_take_precedence(self):
        old = review(decision="unblock", david_override={"rule": "Q20", "david": "leave it"})
        self.assertEqual(memory.effective_decision(old), "leave")
        correction = {"kind": "override", "run": 1, "decision": "wait_for_david", "david": "wait"}
        rows = memory.read("\n".join(json.dumps(r) for r in [old, correction]))
        self.assertEqual(len(rows), 1)
        self.assertEqual(memory.effective_decision(rows[0]), "wait_for_david")
        old["david_override"] = {"rule": "unknown", "david": "no"}
        self.assertIsNone(memory.effective_decision(old))

    def test_bad_memory_is_not_silently_ignored(self):
        for text in ('{"run":', '{}', 'null', '[]', json.dumps(review()) + '\nnope'):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "invalid record"):
                memory.read(text)

    def test_history_counts_picks_and_uses_latest_review(self):
        rows = [review(age=100), review(age=10, run=2, decision="unblock")]
        history = memory.histories(rows)["a"]
        self.assertEqual(history["pick_count"], 2)
        self.assertEqual(history["last_decision"], "unblock")
        self.assertEqual(history["last_review_at"], rows[-1]["ts"])

    def test_legacy_surface_history_does_not_attach_to_an_unknown_session(self):
        rows = [candidate("SURF", review_key="cmux:claude:new-session")]
        memory.rank(rows, [review("SURF", app="cmux")], NOW, 1)
        self.assertEqual(rows[0]["history"]["pick_count"], 0)
        self.assertEqual(rows[0]["eligibility_reason"], "never_reviewed")

    def test_providers_with_the_same_session_id_have_separate_history(self):
        rows = [candidate("SURF", review_key="cmux:codex:session")]
        memory.rank(rows, [review("SURF", review_key="cmux:claude:session")], NOW, 1)
        self.assertEqual(rows[0]["history"]["pick_count"], 0)

    def test_manual_runs_always_use_priority_regardless_of_seed(self):
        for seed in range(1000):
            rows = [candidate("low"), candidate("urgent", pending_interaction=True)]
            selection = memory.rank(rows, [], NOW, seed)
            self.assertEqual(selection["mode"], "priority")
            self.assertEqual(selection["suggested"], "urgent")
            self.assertEqual(selection["chance"], 0)
            self.assertFalse(any(row["spot_check"] for row in rows))

    @patch("memory.SPOT_CHECK_CHANCE", 0.2)
    def test_seed_replays_both_draw_and_weighted_choice(self):
        rows = [candidate(), candidate("b")]
        one, two = copy.deepcopy(rows), copy.deepcopy(rows)
        self.assertEqual(memory.rank(one, [], NOW, 1), memory.rank(two, [], NOW, 1))
        self.assertEqual(one, two)
        self.assertEqual(sum(r["spot_check"] for r in one), 1)

    @patch("memory.SPOT_CHECK_CHANCE", 0.2)
    def test_about_twenty_percent_of_runs_are_random(self):
        count = 0
        for seed in range(2000):
            rows = [candidate(), candidate("b")]
            selection = memory.rank(rows, [], NOW, seed)
            count += selection["mode"] == "spot_check"
            self.assertLessEqual(sum(r["spot_check"] for r in rows), 1)
        self.assertTrue(340 < count < 460, count)

    @patch("memory.SPOT_CHECK_CHANCE", 0.2)
    def test_random_selection_favors_less_recent_reviews(self):
        reviews = [review("recent", decision="unblock", age=60),
                   review("older", decision="unblock", age=86400, run=2)]
        picked = []
        for seed in range(2000):
            result = memory.rank([candidate("recent"), candidate("older")], reviews, NOW, seed)
            if result["mode"] == "spot_check":
                picked.append(result["suggested"])
        self.assertGreater(picked.count("older") / len(picked), 0.95)


if __name__ == "__main__":
    unittest.main()
