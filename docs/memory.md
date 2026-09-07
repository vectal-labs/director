# Scoped correction memory

Keep corrections in the existing `private/log.jsonl` and `private/judgment/`
files. A lesson records what the operator said and where it applies. It does
not automatically authorize an action or choose an intervention.

## Record a correction

Use the original review's run number. `--david` preserves the exact words;
`--rule Qnn` links the corresponding Q&A entry. `--lesson` adds context:

```bash
python3 director/log.py override --run 12 --decision leave --rule Q40 \
  --david "Skip this thread while I investigate. Wait until I release it." \
  --lesson '{"kind":"temporary_instruction","scope":"thread","interpretation":"Leave this thread alone during the investigation","reason":"The operator is investigating it","applies_when":"The original investigation is still open","ends_when":"The operator explicitly releases this hold"}'
```

These are illustrative values; use the real run, words, and situation. Pass
JSON as one argument; use structured subprocess arguments for untrusted text.

Lesson fields:

- `kind`: `general_preference`, `project_decision`, `temporary_instruction`,
  or `exception`.
- `scope`: `thread` (the default), `project`, or `general`. A project decision
  requires project scope. An exception stays within its original session and
  situation. Explicitly supported preferences and temporary instructions may
  have broader scope; a type label alone never justifies generalizing.
- `interpretation`: the agent's understanding, kept separate from `david`.
- `reason`: why the correction matters. If not stated, say that; label any
  inference rather than attributing it to the operator.
- `applies_when`: the circumstances in which this lesson is relevant. A
  matching project or thread alone does not establish that these still hold.
- `ends_when`: an optional ending condition. For example, the operator releases
  a hold, or a named experiment ends. Do not invent a release condition.
- `expires_at`: optional ISO timestamp with timezone, only for an actual stated
  deadline, such as `2026-10-01T12:00:00+02:00`.

The first five fields are required, except scope defaults to `thread`.
A temporary instruction needs `ends_when` or `expires_at`. The CLI rejects
unknown fields and invalid or timezone-free deadlines before appending an event.

The CLI supplies the lesson ID (`run.correction`, such as `12.1`), app, and
scope target from the original review. Record reviews with `--scan`: bb project
IDs and cmux project paths are exact matches. cmux thread lessons use the stable
provider/session key, so moving a terminal preserves them and replacing its
conversation does not inherit them. Old reviews can recover project identity
from their original saved scan; if evidence is missing, record a new review.

## Use and end a lesson

Scans include active, scope-matching lessons on each candidate, including the
original words, Q&A reference, and interpretation. Director must still check
`applies_when`, current instructions, and any conflicts before acting. Explicit
general lessons can appear in either launch app; project and thread lessons
cannot cross app boundaries. Scanning still reads only the launch app.

A deadline removes the lesson from future scan suggestions at that instant.
Natural-language ending conditions require evidence; the script does not guess
whether an investigation finished. Once verified, append an ending event:

```bash
python3 director/log.py end-lesson --id 12.1 \
  --evidence "The operator released this hold in the latest message."
```

This ends only that lesson. It does not change the historical review, corrected
decision, cooldown, or original wording. Other lessons on the same run remain.
Ending an obsolete or withdrawn lesson also uses this command with its evidence.

## Markdown and older history

In `qa.md`, keep exact words and source first, then explicitly labeled
interpretation, kind, scope, reason, applicable situation, and ending condition.
For summaries in `what.md`, `how.md`, or `limits.md`, include the Q&A/lesson ID
and the same boundaries. Mark summaries ended when their lesson ends; do not
leave an obsolete instruction looking current. Corrections outside a reviewed
run can remain in Markdown with these fields; do not invent a review to log them.

Legacy overrides remain valid without `--lesson` and still correct the historical
decision. They are not automatically included as active lessons. Historical
text must be examined in context before reuse; missing scope or dates are
unknown. New corrections append; they do not silently erase independent earlier
lessons. End a superseded lesson explicitly. Never rewrite old logs or bulk
promote past corrections into standing preferences.
