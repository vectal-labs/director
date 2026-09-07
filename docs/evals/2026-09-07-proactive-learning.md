# Proactive learning validation, 2026-09-07

## Change and assumptions

Learning now collects conversation context independently of intervention scans.
Running and recently contacted agents remain protected from intervention, while
their conversations can supply learning evidence. Retained earlier sessions,
including prior Director sessions, can be loaded at startup and by project/session.
Questions and exact answers persist without inventing a review run or promoting
inferred preferences.

The implementation uses the existing local file layout and app adapters. No model
service, database, background process, or action permission was added. The Director
agent decides what question is useful; deterministic code keeps sources and records.

## Research

3 separate DeepAPI research calls completed:

- Memory/provenance: `c7726a33-57bb-4ff0-8a8f-913307a7161e`.
- Event attribution/context: `e2784f7a-64e3-4291-8ead-c41c5359967d`.
- Proactive question timing: `bef39663-0fe0-4251-b9e2-5f067a1b2c6e`.

The design inference is to retrieve concrete episodes before interpreting a
preference, and to ask about a consequential uncertainty in the current task.
Research on [learning preferences from user edits](https://arxiv.org/html/2404.15269v1)
finds that contextual, interpretable preference descriptions can support
personalization without fine-tuning. Research on [proactive programming
assistants](https://arxiv.org/html/2410.04596v1) examines the importance of workspace
context and deciding when to present assistance. Neither establishes that this
Director's questions are useful; that still needs feedback during real use.

## Live bb measurements

Read-only measurement used 12 distinct local threads across 5 projects, including
2 running threads in the captured sample. No agent was messaged or changed.
Captured raw events remain in private thread storage, outside the repository.

- 156 explicitly user-initiated requests, 69 agent-initiated requests, and 8
  system-initiated requests were present.
- All 12 conversations loaded successfully when replayed through the collector.
- All 156 human requests were retained with human attribution. Agent/system input
  was separately labeled. Counts were checked against the original event census.
- bb supplies `systemMessageKind: "unlabeled"` on ordinary human requests. The first
  implementation incorrectly treated this as system input. Live measurement
  exposed that error; the parser and the CLI fixture now cover the actual shape.
- The existing review scan omits running threads. The new CLI workflow test proves
  learning can read a human correction there without making it a review candidate.
- A fresh end-to-end observation command read 12 live conversations successfully.
  Limits and truncated context are explicit; this is not exhaustive history recall.

## Automated validation and limits

The full suite passed: 222 tests. This includes 14 new learning workflow tests
covering human attribution, archived sessions, running agents, source validation,
restart recall, duplicate questions, dismissals, limits, malformed history, app
isolation, retained cmux transcripts, and terminal reuse. The existing launcher
PTY test needs host terminal access; the full suite passed with that access.
The final suite includes the live attribution correction and recall of an earlier
Director conversation on a reused cmux surface.

The cmux path was validated with fake CLI sessions, a registered transcript, and
screen fixtures. It was not tested against a live cmux instance. Unattributed
transcript/screen text is not labeled as human speech. A missing transcript is an
explicit gap. Question relevance and interruption frequency need evaluation in
actual Director learning turns; fixture tests do not establish those outcomes.
