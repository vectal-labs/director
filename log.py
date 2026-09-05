#!/usr/bin/env python3
"""Record a review or the operator's correction. Events append to private/log.jsonl under a file lock."""
import argparse
import fcntl
import json
import pathlib

import bb_app
import cmux_app
import memory
from scan import launch_app


def load_scan(path):
    path = pathlib.Path(path)
    if not path.is_absolute() and not path.exists():
        path = memory.PRIVATE / path
    scan = json.loads(path.read_text())
    if not isinstance(scan, dict):
        raise ValueError("scan must be an object")
    return scan


def scan_record(path, picked):
    if not path:
        return {}
    scan = load_scan(path)
    candidate = next((r for r in scan["candidates"] if r["id"] == picked), None)
    if candidate is None:
        raise ValueError(f"{picked} is not in the supplied scan")
    review_key = candidate.get("review_key")
    if scan.get("app") == "cmux" and not review_key and candidate.get("provider") and candidate.get("session"):
        review_key = f"cmux:{candidate['provider']}:{candidate['session']}"
    return {"app": scan.get("app"), "review_key": review_key,
            "reviewed_state": candidate.get("state"), "scan_selection": scan.get("selection"),
            "selection_reason": candidate.get("eligibility_reason"), "spot_check": candidate.get("spot_check", False)}


def find_run(rows, number):
    review = next((row for row in rows if row["run"] == number), None)
    if review is None:
        raise ValueError(f"no run {number}")
    return review


def observe_resume(review):
    """One read-only check of the recorded agent in the current launch app."""
    app = review.get("app")
    if app not in {"bb", "cmux"}:
        raise ValueError("confirmation requires a review saved with --scan")
    launch_app(app)  # Never inspect an app this Director was not launched in.
    observed = {"id": review["picked"], "status": "unknown"}
    try:
        if app == "bb":
            thread = bb_app.bb("thread", "show", review["picked"])["thread"]
            if thread["id"] != review["picked"]:
                raise ValueError("bb returned a different thread")
            observed.update(status=thread["status"])
        else:
            if not review.get("review_key"):
                raise ValueError("confirmation requires the recorded cmux session")
            saved = cmux_app.cmux("sessions", "list", "--all")
            live, _ = cmux_app.terminals(cmux_app.cmux("tree", "--all", "--id-format", "both"))
            sessions = [s for s in saved["sessions"] if s.get("active_for_surface")
                        and f"cmux:{s.get('agent')}:{s.get('session_id')}" == review["review_key"]
                        and str(s.get("surface_id", "")).upper() in live]
            if sessions:
                session = max(sessions, key=lambda s: s.get("updated_at_unix") or 0)
                observed.update(id=session["surface_id"].upper(), status=cmux_app.status(session),
                                review_key=review["review_key"])
    except (*bb_app.ERRORS, OSError, AttributeError) as error:
        observed.update(status="unknown", error=type(error).__name__, detail=str(error))
    observed["observed_at"] = memory.timestamp()
    return observed


def record(args):
    snapshot = scan_record(args.scan, args.picked) if args.cmd == "run" else {}
    expected, observed = None, None
    if args.cmd == "confirm":
        review = find_run(memory.read(), args.run)
        if review.get("action_status") == "resumed":
            return print(f"run {args.run} already confirmed")
        if review.get("action_status") not in {"sent", "queued"}:
            raise ValueError("only a sent or queued action can be confirmed")
        expected = review["outcome"]
        observed = observe_resume(review)
    memory.LOG.parent.mkdir(exist_ok=True)
    with memory.LOG.open("a+") as file:
        fcntl.flock(file, fcntl.LOCK_EX)
        file.seek(0)
        contents = file.read()
        rows = memory.read(contents)
        if args.cmd == "run":
            if args.action and args.proposed_action:
                raise ValueError("save --proposed-action first, then record the actual --action with outcome")
            if args.proposed_action is not None and not args.proposed_action.strip():
                raise ValueError("--proposed-action must not be blank")
            now = memory.timestamp()
            row = {"run": max((r["run"] for r in rows), default=0) + 1, "ts": now, "reviewed_at": now,
                   "picked": args.picked, "title": args.title, "decision": args.decision,
                   "seen": args.seen, "reason": args.reason, "action": args.action, "rule": args.rule,
                   "proposed_action": args.proposed_action,
                   "action_status": "unknown" if args.action else "proposed" if args.proposed_action else "none",
                   "candidates": args.candidates, "skipped": args.skipped, "scan": args.scan,
                   "david_override": None, **snapshot}
        elif args.cmd == "override":
            find_run(rows, args.run)
            row = {"kind": "override", "run": args.run, "ts": memory.timestamp(),
                   "david": args.david, "decision": args.decision, "rule": args.rule}
        else:
            review = find_run(rows, args.run)
            if args.cmd == "confirm":
                if review.get("outcome") != expected:
                    raise ValueError("action changed during confirmation; check it again")
                resumed = observed["status"] == {"bb": "active", "cmux": "running"}[review["app"]]
                status = "resumed" if resumed else review["action_status"]
                detail = "observed the agent running" if resumed else f"not confirmed: {observed['status']}"
                row = {"kind": "outcome", "run": args.run, "ts": memory.timestamp(),
                       "status": status, "detail": detail, "observation": observed}
            else:
                if args.scan and load_scan(args.scan).get("app") != review.get("app"):
                    raise ValueError("recheck scan belongs to a different app")
                if args.status == "skipped" and args.action:
                    raise ValueError("a skipped action was not sent")
                row = {"kind": "outcome", "run": args.run, "ts": memory.timestamp(),
                       "status": args.status, "detail": args.detail, "scan": args.scan,
                       "action": None if args.status == "skipped" else
                       args.action or review.get("action") or review.get("proposed_action")}
            memory.apply_outcome(review, row)
        prefix = "\n" if contents and not contents.endswith("\n") else ""
        file.write(prefix + json.dumps(row, ensure_ascii=False) + "\n")
    result = row.get("decision") or row["status"]
    print(f"{args.cmd} {row['run']} logged: {result}" + (f" ({row['detail']})" if args.cmd == "confirm" else ""))


def stats():
    rows = memory.read()
    if not rows:
        return print("no runs")
    by = {d: sum(memory.effective_decision(r) == d for r in rows) for d in memory.DECISIONS}
    unknown = sum(memory.effective_decision(r) is None for r in rows)
    overrides = sum(bool(r.get("david_override")) for r in rows)
    print(f"runs {len(rows)} | decisions: " + " ".join(f"{key} {value}" for key, value in by.items())
          + f" | unknown {unknown} | overrides {overrides} ({100 * overrides // len(rows)}%)")
    statuses = [r.get("action_status", "unknown") for r in rows]
    print(f"actions: proposals {sum(bool(r.get('proposed_action')) for r in rows)}"
          f" pending {statuses.count('proposed')} | "
          + " ".join(f"{status} {statuses.count(status)}" for status in memory.OUTCOMES)
          + f" | confirmed_resumes {statuses.count('resumed')} legacy_unknown {statuses.count('unknown')}")
    for row in rows[-5:]:
        flag = " OVERRIDDEN" if row.get("david_override") else ""
        decision = memory.effective_decision(row) or "unknown"
        print(f"  #{row['run']} {row['ts'][5:16]} {decision:14} {row['picked']} {row.get('title', '')[:35]}"
              f" [{row.get('action_status', 'unknown')}]{flag}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("--picked", required=True)
    run.add_argument("--title", default="")
    run.add_argument("--decision", required=True, choices=memory.DECISIONS)
    run.add_argument("--seen", required=True)
    run.add_argument("--reason", required=True)
    run.add_argument("--proposed-action", help="exact proposed message, interaction response, or resume command; unsent")
    run.add_argument("--action", help="legacy action text; outcome remains unknown, never assumed resumed")
    run.add_argument("--rule")
    run.add_argument("--candidates", type=int)
    run.add_argument("--skipped", type=int)
    run.add_argument("--scan", help="saved scan; supplies the app, reviewed state, and selection details")
    override = sub.add_parser("override")
    override.add_argument("--run", type=int, required=True)
    override.add_argument("--david", required=True, help="the operator's exact words")
    override.add_argument("--decision", required=True, choices=memory.DECISIONS)
    override.add_argument("--rule")
    outcome = sub.add_parser("outcome", help="append a delivery result or skipped action to an existing review")
    outcome.add_argument("--run", type=int, required=True)
    outcome.add_argument("--status", choices=memory.OUTCOMES, required=True)
    outcome.add_argument("--detail", required=True, help="actual command result or why the action was skipped")
    outcome.add_argument("--action", help="exact action attempted, if different from the proposal")
    outcome.add_argument("--scan", help="optional recheck scan; the target may now be absent")
    confirm = sub.add_parser("confirm", help="check once whether the sent action's agent is now running; read-only")
    confirm.add_argument("--run", type=int, required=True)
    sub.add_parser("stats")
    args = parser.parse_args()
    try:
        stats() if args.cmd == "stats" else record(args)
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
