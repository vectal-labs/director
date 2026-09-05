# Director

Director is an agent that keeps your other coding agents running. It runs inside bb or cmux, scans that app for stopped agents, reads one, and either leaves it, messages it to get it going again, or asks you when the next move is really yours. Every review is logged; every correction you give becomes a rule.

It watches only the app it was launched in. A Director in bb never touches cmux, and the other way round.

## Layout

- `ROLE.md` is the single system prompt: who the Director is, its loop, its limits. No app commands.
- `.agents/skills/director-bb/` and `.agents/skills/director-cmux/` hold the app mechanics. The Director reads the one for its launch app. `.claude/skills` links there so Claude Code finds them too.
- `scan.py` finds stopped agents in the launch app, ranks them with the review memory, and saves the scan. Read-only.
- `bb_app.py` and `cmux_app.py` are the two read-only adapters. `memory.py` holds the review memory, cooldown, and the dormant sampler. `log.py` records reviews, your corrections, and stats.
- `private/` is gitignored and holds everything personal: your rules (`private/judgment/`), the review log (`private/log.jsonl`), and saved scans (`private/scans/`).

## Setup

Requirements: Python 3, plus `bb` on `PATH` for bb or the cmux app for cmux (its CLI is found on `PATH` or inside `/Applications/cmux.app`). No dependencies.

```bash
git clone <this repo> ~/code/director
mkdir -p ~/code/director/private/judgment
```

Write your rules into `private/judgment/what.md`, `how.md`, and `limits.md`, and keep every question you answer in `private/judgment/qa.md`, numbered Qnn. The Director reads these before acting and refuses to unblock anything without them.

cmux only: agents are visible through cmux's hooks. Run `cmux hooks setup` once so Claude Code, Codex, Pi, and the rest report their state.

## Start the Director

Use a frontier model. Start the agent from this repo's directory and give it one message:

```
Read ROLE.md and be the Director.
```

- bb: open a thread on this repo (project Director) and send that message, or `bb thread spawn --project <id> --provider <provider> --model <model> --title DIRECTOR --prompt "Read ROLE.md and be the Director."`
- cmux: open a terminal in this repo, start your agent (`claude`, `codex`, ...), and send that message.

The agent finds its app from the environment (`BB_THREAD_ID` or `CMUX_SURFACE_ID`), reads the matching skill, and runs `python3 scan.py`. During the manual stage it asks you before every message, approval, or denial. Run the loop by waking it up: "scan" or "next".

## App selection

There is no switch. `scan.py` reads `BB_THREAD_ID` and `CMUX_SURFACE_ID`, and refuses to run when neither or both are set (pass `--app bb|cmux` only in the both case, and only for the app you are really inside). The bb adapter calls `bb` only; the cmux adapter calls `cmux` and reads `~/.cmuxterm` only. Tests assert this with fake binaries that fail when the wrong app is called.

## Commands

```bash
python3 scan.py [--hours N] [--seed N]                   # read-only scan, saved to private/scans/
python3 log.py run --picked <id> --decision leave ...     # record a review (see ROLE.md for all flags)
python3 log.py override --run N --david "..." --decision unblock --rule Qnn
python3 log.py stats
python3 -m unittest discover -s . -p 'test_*.py'          # CLI tests use fake bb and cmux, never real agents
```

## Behavior that is deliberately preserved

- Manual approval: the Director asks before every message or interaction response.
- Random spot checks are off (`SPOT_CHECK_CHANCE = 0.0`, Q27). The weighted sampler stays in `memory.py` so past scans replay with `--seed`.
- Review memory: an unchanged `leave` sleeps 60 minutes; any meaningful change brings a thread back at once. Old log rows and scans stay valid.
- Agents the operator wrote to in the last 3 minutes are skipped.

## Changes from the source implementation

Extracted on 2026-09-05 from `platform-for-agents/director/`, which stays untouched until this repo is validated.

- One app-neutral `ROLE.md` replaces the bb-specific prompt; bb commands moved into `director-bb`, and a new `director-cmux` skill covers cmux.
- `scan.py` split into `scan.py` (app detection, ranking, saving) plus `bb_app.py` (the old bb logic) and `cmux_app.py` (new).
- Personal files moved under `private/`: `what.md`, `how.md`, `limits.md`, `qa.md` into `private/judgment/`; `log.jsonl` and `scans/` into `private/`. Scan references such as `scans/<name>.json` resolve there, so old log rows still work.
- Scan fields `david_at_ms`, `david_min_ago`, and `skipped_david_recent` are now `user_at_ms`, `user_min_ago`, and `skipped_user_recent`. The log schema (`wait_for_david`, `david_override`, `--david`) is unchanged.
- Tests: `test_scan.py` became `test_bb.py`; `test_cli.py` gained fake-cmux runs and app-isolation checks; `test_cmux.py` is new. No test was removed or weakened.
