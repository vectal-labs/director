#!/usr/bin/env python3
"""Record a review or the operator's correction. Events append to private/log.jsonl under a file lock."""
import argparse
import fcntl
import json
import pathlib

import memory


def scan_record(path, picked):
    if not path:
        return {}
    path = pathlib.Path(path)
    if not path.is_absolute() and not path.exists():
        path = memory.PRIVATE / path
    scan = json.loads(path.read_text())
    candidate = next((r for r in scan["candidates"] if r["id"] == picked), None)
    if candidate is None:
        raise ValueError(f"{picked} is not in the supplied scan")
    review_key = candidate.get("review_key")
    if scan.get("app") == "cmux" and not review_key and candidate.get("provider") and candidate.get("session"):
        review_key = f"cmux:{candidate['provider']}:{candidate['session']}"
    return {"app": scan.get("app"), "review_key": review_key,
            "reviewed_state": candidate.get("state"), "scan_selection": scan.get("selection"),
            "selection_reason": candidate.get("eligibility_reason"), "spot_check": candidate.get("spot_check", False)}


def record(args):
    snapshot = scan_record(args.scan, args.picked) if args.cmd == "run" else {}
    memory.LOG.parent.mkdir(exist_ok=True)
    with memory.LOG.open("a+") as file:
        fcntl.flock(file, fcntl.LOCK_EX)
        file.seek(0)
        contents = file.read()
        rows = memory.read(contents)
        if args.cmd == "run":
            now = memory.timestamp()
            row = {"run": max((r["run"] for r in rows), default=0) + 1, "ts": now, "reviewed_at": now,
                   "picked": args.picked, "title": args.title, "decision": args.decision,
                   "seen": args.seen, "reason": args.reason, "action": args.action, "rule": args.rule,
                   "candidates": args.candidates, "skipped": args.skipped, "scan": args.scan,
                   "david_override": None, **snapshot}
        else:
            if not any(r["run"] == args.run for r in rows):
                raise ValueError(f"no run {args.run}")
            row = {"kind": "override", "run": args.run, "ts": memory.timestamp(),
                   "david": args.david, "decision": args.decision, "rule": args.rule}
        prefix = "\n" if contents and not contents.endswith("\n") else ""
        file.write(prefix + json.dumps(row, ensure_ascii=False) + "\n")
    print(f"{args.cmd} {row['run']} logged: {args.decision}")


def stats():
    rows = memory.read()
    if not rows:
        return print("no runs")
    by = {d: sum(memory.effective_decision(r) == d for r in rows) for d in memory.DECISIONS}
    unknown = sum(memory.effective_decision(r) is None for r in rows)
    overrides = sum(bool(r.get("david_override")) for r in rows)
    print(f"runs {len(rows)} | " + " ".join(f"{key} {value}" for key, value in by.items())
          + f" | unknown {unknown} | overrides {overrides} ({100 * overrides // len(rows)}%)")
    for row in rows[-5:]:
        flag = " OVERRIDDEN" if row.get("david_override") else ""
        decision = memory.effective_decision(row) or "unknown"
        print(f"  #{row['run']} {row['ts'][5:16]} {decision:14} {row['picked']} {row['title'][:35]}{flag}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("--picked", required=True)
    run.add_argument("--title", default="")
    run.add_argument("--decision", required=True, choices=memory.DECISIONS)
    run.add_argument("--seen", required=True)
    run.add_argument("--reason", required=True)
    run.add_argument("--action")
    run.add_argument("--rule")
    run.add_argument("--candidates", type=int)
    run.add_argument("--skipped", type=int)
    run.add_argument("--scan", help="saved scan; supplies the app, reviewed state, and selection details")
    override = sub.add_parser("override")
    override.add_argument("--run", type=int, required=True)
    override.add_argument("--david", required=True, help="the operator's exact words")
    override.add_argument("--decision", required=True, choices=memory.DECISIONS)
    override.add_argument("--rule")
    sub.add_parser("stats")
    args = parser.parse_args()
    try:
        stats() if args.cmd == "stats" else record(args)
    except (ValueError, OSError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
