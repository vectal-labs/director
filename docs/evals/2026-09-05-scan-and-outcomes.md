# Scan safety and action outcomes

Scope: preserve exited-agent support, report uncertain input history, and separate
proposals, delivery, and observed resumes. No agent messages were sent during validation.

## Assumptions checked

- Missing input history cannot establish that the operator has not written recently.
- cmux stores provider/session identity separately from terminal location. Its
  [hook documentation](https://github.com/manaflow-ai/cmux/blob/main/docs/agent-hooks.md)
  describes session IDs, surface IDs, lifecycle, and process metadata.
- Queueing and execution are separate facts. The
  [Temporal event documentation](https://docs.temporal.io/workflow-execution/event)
  records scheduling and execution outcomes separately. Director uses the existing
  JSONL log, adding outcome events to the original review.
- Approval precedes execution; it does not prove execution happened. The
  [Claude SDK approval workflow](https://code.claude.com/docs/en/agent-sdk/user-input)
  pauses execution for a human response. Director retains manual approval and
  rechecks current state afterward.

DeepAPI research requests, all succeeded with complete results:
`f1c93ff0-0799-432f-a7c4-fd4b07330942` (outcomes),
`abb56fac-ba65-4b3f-8ff0-aa0517c9e23b` (cmux data),
`eb1f2c52-9179-4f48-8124-d8016d6f4005` (approval workflow).

## Read-only local measurement

- Compared 20 distinct real stopped bb thread histories against baseline `e0fd206`.
  All 20 parsed successfully. Fingerprints, last human input, message tails,
  recent errors, and pending-interaction flags matched the baseline in every case.
  These cases were idle threads; malformed/error cases are covered by fixtures.
- Full bb scan: 389 listed, 27 candidates, zero recently-contacted exclusions and
  zero read/validation errors at measurement time.
- Real cmux event history: 3,800 valid JSON events, including 104 prompt events.
  Updated parsing recovered input timestamps for 13 surfaces with zero errors
  and no unknown surfaces. Saved session listing contained 25 sessions.
- Existing private review log: all 20 reviews and six overrides remained readable.
  None were incorrectly counted as confirmed resumes.
- Live cmux topology was unavailable because the app was closed. Open/closed
  terminals, session movement, exited processes, and confirmation use fake-CLI
  integration tests; live cmux behavior is not claimed as verified.

## Regression and workflow checks

`python3 -m unittest discover -s . -p 'test_*.py'`: 66 tests pass (baseline: 44).
New cases reproduced the failures before implementation. They cover unavailable
input history, malformed records, human input arriving during screen reads,
unsent/queued/failed/skipped actions, session identity, legacy logs, confirmation,
and preservation of the exact approved action through queue delivery.

Three fresh read-only agent evaluations compared baseline and updated skills:
cancel after fresh human input; cmux input history unavailable; queued delivery
followed by an idle observation. Updated instructions produced the expected
skipped or unconfirmed outcome in all three, without sending, resending, or polling.
The review found one stale coverage description, which was corrected.

`confirmed_resumes` means the recorded agent was observed running after a sent
or queued action. It does not claim task completion or prove causation. If work
starts and finishes before the single check, it remains unconfirmed.
