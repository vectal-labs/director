# Selection priorities

Edit `profile/priorities.json` in the Director checkout you run. It is personal,
gitignored configuration. The next scan reads it; no restart is needed.

```json
{
  "projects": {"proj_example": 0.5},
  "threads": {"thr_example": 2.0}
}
```

Replace the example IDs with the `project` and `id` values from a bb scan.
Thread weights override project weights, rather than multiplying them.
Unconfigured threads default to `1.0`. Renaming a bb project or thread has no effect.
For cmux, project keys are the reported `project` paths and thread keys are the
stable `review_key` session identities, not reusable surface IDs.

Weights must be finite numbers greater than zero. Remove an entry to restore
inheritance; zero does not disable a thread. A missing file means all defaults.
Malformed JSON, unknown fields, or invalid weights stop the scan with an error.

Ranking stays deterministic, in this order:

1. Eligibility (including existing history checks and cooldowns).
2. Existing urgency rules: pending interactions, then errors, then questions.
3. Higher priority weight.
4. Existing tie-breakers: idle time, then thread ID.

Running threads and recent human messages remain excluded. A higher weight
cannot override these checks or urgency. `0.5` means lower priority among
otherwise comparable threads; it does not promise half as many selections.
Equal weights keep the old order. Random checks default to disabled; the historical
sampler is unchanged. The suggestion remains subject to Director's judgment
and manual approval before any agent action.

Each saved scan includes `priority_config` plus every candidate's
`priority_weight`, `priority_source` (`thread`, `project`, or `default`), and
`priority_key`. This records why a weight applied even after settings change.
Priority changes do not change the thread's state snapshot or reset cooldowns.

Validation: `python3 -m unittest discover -s tests -p 'test_*.py'`.
