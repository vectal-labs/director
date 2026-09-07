# Technical reference

For installation and launch, see the [quick start](../README.md#quick-start) and [installer lifecycle](install.md). The commands below remain available for source checkouts.

## Setup and personal rules

`python3 director/setup.py --app bb` (or `--app cmux`) checks macOS, Python, and the chosen CLI. It creates missing files in `private/judgment/` and keeps existing files. You can rerun it safely. Setup has been tested with Python 3.9.6 and 3.14.6.

- `what.md`: what Director should do.
- `how.md`: how it should work.
- `limits.md`: when it must ask you.
- `qa.md`: your answers and corrections, numbered Q01, Q02, and so on.

Read and edit the examples before starting. Share the Git repo with teammates; each person keeps their own rules and history in gitignored `private/`.

For cmux, run `cmux hooks setup` once so Claude Code, Codex, Pi, and other agents report their state.

## Launch from the bb CLI

Replace the placeholders with your project, provider, and model:

```bash
bb thread spawn --project <id> --provider <provider> --model <model> \
  --title DIRECTOR --prompt "Read ROLE.md and be the Director."
```

The agent detects its launch app, reads the matching skill, and runs `python3 director/scan.py`. Say “scan” or “next” to request another review. During the manual stage, it asks before every message, approval, denial, or retry.

## Repository layout

- [`ROLE.md`](../ROLE.md) is the system prompt: Director’s role, loop, and limits. App commands live in the skills.
- [The bb skill](../.agents/skills/director-bb/SKILL.md) and [the cmux skill](../.agents/skills/director-cmux/SKILL.md) hold their app’s mechanics. Director reads the one for its launch app. `.claude/skills` links to `.agents/skills` so Claude Code finds them too.
- [`scan.py`](../director/scan.py) finds stopped agents, ranks them using review memory, and saves a scan. Scanning is read-only.
- [`bb_app.py`](../director/bb_app.py) and [`cmux_app.py`](../director/cmux_app.py) are the read-only app adapters.
- [`memory.py`](../director/memory.py) holds review memory, cooldown, and the dormant sampler. [`log.py`](../director/log.py) records reviews, corrections, and stats.
- `tests/` holds the unit tests and fake-CLI workflow tests. `docs/` holds scope, open questions, and reference material.
- `private/` holds personal rules in `judgment/`, the review log in `log.jsonl`, and saved scans in `scans/`. It is gitignored.

## App selection

`director/scan.py` detects the launch app from `BB_THREAD_ID` or `CMUX_SURFACE_ID`. It refuses to run when neither or both are set. If both are set, pass `--app bb|cmux` only for the app you are actually inside.

The bb adapter calls `bb` only. The cmux adapter calls `cmux` and reads `~/.cmuxterm` only. Tests enforce this separation with fake binaries that fail if the wrong app is called.

## Commands

```bash
# Read-only scan, saved to private/scans/
python3 director/scan.py [--hours N] [--seed N]

# Record an operator correction
python3 director/log.py override --run N --david "..." --decision unblock --rule Qnn

# Review statistics
python3 director/log.py stats

# Run tests with fake app CLIs
python3 -m unittest discover -s tests -p 'test_*.py'
```

See [ROLE.md](../ROLE.md#what-you-do) for the `director/log.py run` flags and review workflow, and the app skills for delivery outcomes and confirmation commands. CLI tests never message real agents.

## Review behavior

- Manual approval is required before every message or interaction response.
- Random spot checks are off (`SPOT_CHECK_CHANCE = 0.0`, Q27). The weighted sampler stays in `memory.py` so past scans replay with `--seed`.
- An unchanged `leave` decision sleeps for 60 minutes after the last recorded review. A meaningful state change makes the agent eligible immediately. Scanning alone does not reset the cooldown.
- Historical logs and scans stay valid. Old records without a state snapshot remain eligible until reviewed with a new scan.
- Agents the operator wrote to in the last 3 minutes are skipped.
