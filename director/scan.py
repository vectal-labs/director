#!/usr/bin/env python3
"""Scan the app Director was launched in for stopped agents. Save JSON with review history and a reproducible suggestion.

The launch app is detected from the environment: BB_THREAD_ID means bb, CMUX_SURFACE_ID means cmux.
Director watches only that app. This script only reads; it never messages an agent or resolves a prompt.
"""
import argparse
import datetime as dt
import json
import os
import random
import time

import bb_app
import cmux_app
import memory

APPS = {"bb": bb_app, "cmux": cmux_app}


def launch_app(choice, environ=os.environ):
    present = [name for name, module in APPS.items() if environ.get(module.SELF_VAR)]
    if choice:
        if choice not in present:
            raise ValueError(f"--app {choice} but {APPS[choice].SELF_VAR} is not set; Director watches only the app it was launched in")
        return choice
    if len(present) != 1:
        raise ValueError("cannot tell the launch app: exactly one of BB_THREAD_ID or CMUX_SURFACE_ID must be set, or pass --app")
    return present[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", choices=sorted(APPS), help="only needed when both apps' variables are set")
    parser.add_argument("--self", help="own thread or surface id; defaults to the launch app's variable")
    parser.add_argument("--hours", type=float, help="optional recency filter; default is all stopped agents")
    parser.add_argument("--seed", type=int, help="reproduce the random draw")
    args = parser.parse_args()
    if args.hours is not None and args.hours <= 0:
        parser.error("--hours must be positive")
    try:
        app = launch_app(args.app)
        priorities = memory.read_priorities()
    except ValueError as error:
        parser.error(str(error))
    module = APPS[app]
    self_id = args.self or os.environ[module.SELF_VAR]
    now = time.time()
    try:
        runs = memory.read()
    except (ValueError, OSError) as error:
        parser.exit(1, f"memory read failed: {error}\n")
    try:
        rows, skipped, errors, extras = module.scan(self_id, now, args.hours)
    except module.ERRORS as error:
        parser.exit(1, f"{app} scan failed: {error}\n")
    seed = args.seed if args.seed is not None else random.SystemRandom().getrandbits(64)
    memory.attach_lessons(rows, runs, now, app)
    selection = memory.rank(rows, runs, now, seed, priorities)
    memory.SCANS.mkdir(parents=True, exist_ok=True)
    path = memory.SCANS / dt.datetime.now().strftime("%Y-%m-%d-%H%M%S-%f.json")
    result = {"app": app, "self": self_id, "scanned_at": dt.datetime.fromtimestamp(now).astimezone().isoformat(),
              "duration_ms": round((time.time() - now) * 1000), "hours": args.hours, **extras,
              "scan": f"scans/{path.name}", "selection": selection, "priority_config": priorities, "candidates": rows,
              "skipped_user_recent": skipped, "coverage": "partial" if errors else "full", "errors": errors}
    output = json.dumps(result, indent=1, ensure_ascii=False)
    path.write_text(output)
    print(output)


if __name__ == "__main__":
    main()
