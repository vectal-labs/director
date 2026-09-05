"""JSONL review history and the scan's repeat-review and sampling rules."""
import datetime as dt
import json
import pathlib
import random

HERE = pathlib.Path(__file__).parent
PRIVATE = HERE / "private"  # gitignored: operator rules, review memory, saved scans
LOG = PRIVATE / "log.jsonl"
SCANS = PRIVATE / "scans"
DECISIONS = ("unblock", "leave", "wait_for_david", "deny")
RECHECK_SECONDS = 3600
SPOT_CHECK_CHANCE = 0.0  # Q27: disabled during manual fine-tuning.


def timestamp():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def effective_decision(row):
    correction = row.get("david_override")
    if not correction:
        return row["decision"]
    # These two legacy corrections predate the explicit decision field (private/judgment/qa.md).
    return correction.get("decision") or {"Q20": "leave", "Q21": "unblock"}.get(correction.get("rule"))


def read(text=None):
    if text is None:
        text = LOG.read_text() if LOG.exists() else ""
    runs = {}
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            run = row["run"]
            if row.get("kind") == "override":
                if row["decision"] not in DECISIONS:
                    raise ValueError("invalid correction")
                runs[run]["david_override"] = row
            else:
                if run in runs or row["decision"] not in DECISIONS:
                    raise ValueError("invalid run")
                row["picked"]
                dt.datetime.fromisoformat(row["ts"])
                runs[run] = row
        except (ValueError, KeyError, TypeError) as error:
            raise ValueError(f"log.jsonl line {number}: invalid record") from error
    return list(runs.values())


def histories(runs):
    result = {}
    for row in runs:
        picked = row.get("review_key") or row["picked"]
        count = result.get(picked, {}).get("pick_count", 0) + 1
        result[picked] = {
            "pick_count": count, "last_run": row["run"],
            "last_review_at": row.get("reviewed_at", row["ts"]),
            "last_decision": effective_decision(row),
            "last_override": row.get("david_override"),
            "reviewed_state": row.get("reviewed_state"),
        }
    return result


def rank(candidates, runs, now, seed):
    history = histories(runs)
    for row in candidates:
        previous = history.get(row.get("review_key") or row["id"])
        elapsed, changed, reason = None, None, "never_reviewed"
        if previous:
            elapsed = max(0, now - dt.datetime.fromisoformat(previous["last_review_at"]).timestamp())
            before = previous["reviewed_state"]
            changed = before != row["state"] if before else None
            if changed is None:
                reason = "state_unknown"
            elif changed:
                reason = "state_changed"
            elif previous["last_decision"] != "leave":
                reason = "needs_review"
            else:
                reason = "leave_expired" if elapsed >= RECHECK_SECONDS else "leave_cooldown"
        row.update(history=previous or {"pick_count": 0}, changed_since_review=changed,
                   review_eligible=reason != "leave_cooldown", eligibility_reason=reason,
                   review_age_min=round(elapsed / 60, 2) if elapsed is not None else None,
                   recheck_in_sec=max(0, RECHECK_SECONDS - elapsed) if reason == "leave_cooldown" else 0,
                   spot_check=False)
        # Linear age in minutes, capped at one day; unseen threads get the cap.
        row["random_weight"] = max(1, min(1440, elapsed / 60)) if elapsed is not None else 1440
    candidates.sort(key=lambda r: (not r["review_eligible"], not r["pending_interaction"],
                                   r["recent_error"] is None, not r["ends_with_question"],
                                   r["idle_min"], r["id"]))
    eligible = [r for r in candidates if r["review_eligible"]]
    rng = random.Random(seed)
    draw = rng.random()
    selection = {"seed": seed, "draw": draw, "chance": SPOT_CHECK_CHANCE,
                 "mode": "none", "suggested": None, "eligible_count": len(eligible)}
    if eligible:
        spot = draw < SPOT_CHECK_CHANCE
        picked = rng.choices(eligible, weights=[r["random_weight"] for r in eligible])[0] if spot else eligible[0]
        picked["spot_check"] = spot
        selection.update(mode="spot_check" if spot else "priority", suggested=picked["id"])
    return selection
