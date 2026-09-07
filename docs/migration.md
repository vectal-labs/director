# Migration history

Director was extracted on 2026-09-05 from `platform-for-agents/director/`. The extraction left the source implementation untouched pending validation.

- One app-neutral `ROLE.md` replaced the bb-specific prompt. bb commands moved into `director-bb`, and a new `director-cmux` skill covers cmux.
- `scan.py` split into `scan.py` (app detection, ranking, saving), `bb_app.py` (the old bb logic), and `cmux_app.py` (new).
- Personal files moved under `private/`: `what.md`, `how.md`, `limits.md`, and `qa.md` into `private/judgment/`; `log.jsonl` and `scans/` into `private/`. Scan references such as `scans/<name>.json` resolve there, so old log rows still work.
- Scan fields `david_at_ms`, `david_min_ago`, and `skipped_david_recent` became `user_at_ms`, `user_min_ago`, and `skipped_user_recent`. The log schema (`wait_for_david`, `david_override`, `--david`) stayed unchanged.
- `test_scan.py` became `test_bb.py`. `test_cli.py` gained fake-cmux runs and app-isolation checks; `test_cmux.py` was added. No test was removed or weakened.

See the [technical reference](reference.md) for current behavior and commands, or return to the [README](../README.md).

## Profile separation, 2026-09-07

Personal teaching and settings now live in `profile/`; operational data lives in
`state/`. `private/` remains available for unrelated private notes. This replaces
the personal-data placement described in the original extraction above.

The migration preserves originals, historical scan references, scoped lessons,
and existing behavior. Shared instructions load the profile instead of embedding
the operator's style or numbered corrections. See [profile.md](profile.md) for
the file contract, migration, and backup procedure.
