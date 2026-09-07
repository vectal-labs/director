# Learning from real work

Learning has 3 inputs: direct teaching, observed operator behavior, and previous
agent conversations. It runs independently of stopped-agent review selection.
A running agent or recent human message can be useful learning evidence while
remaining ineligible for intervention.

## Observe and recall

At startup and each learning turn, run:

```sh
python3 director/learning.py observe
python3 director/learning.py observe --project <project>
python3 director/learning.py observe --session <session> --messages 100
```

The default loads the 12 most recently updated visible sessions, with up to 40
conversation messages each in bb. This is a starting context window, not a claim
to have read every session. Use the exact project or session to retrieve older
relevant context; there is no age cutoff. Earlier Director conversations count
as past sessions too. Only the current Director is excluded.

bb reads current and archived threads on the launch host, excluding hidden and
deleted threads. It retains explicit human requests, assistant messages, and
separately labeled agent/system input. Missing sender attribution stays unknown.
Non-text attachments are flagged, not interpreted. Each message has a stable
source ID and timestamp. Message text over 6,000 characters is marked truncated.
Read the original thread when the omitted context might change the meaning.

cmux reads its registered sessions, including closed and inactive ones. It reads
the registered transcript's last 24,000 bytes, dropping a partial first record.
Without a transcript, it can read current-session scrollback on an open surface.
This text is unattributed context: verify who spoke before interpreting a choice.
Never treat a predicted prompt as submitted human input. Missing transcripts or
unreadable sessions appear in `errors`, never as empty evidence of human intent.

`limited` reports whether the session limit omitted inventory entries. `truncated`
marks incomplete conversation context. `coverage` describes the selected sessions,
not all historical activity in the app. `--limit` and `--messages` can expand a
needed window. `--session` uses a bb thread ID or cmux session ID/stable review key;
`--project` uses the exact bb project ID or cmux cwd.

Every observation is saved under `state/learning/`. It reloads that app's recorded
questions and answers, including their original source and evidence. A project
filter narrows displayed questions, while `pending_question` still reports any
open question in the app. Snapshots contain private conversation excerpts and
must remain local. They are excluded from Git and releases.

## Ask about an actual decision

Read the source conversation and relevant profile teaching first. Look for an
important unclear reason, a repeated pattern worth confirming, or an apparent
conflict with an earlier preference. Compare the proposal, operator response,
and subsequent result. Distinguish an observed result from a promised action.

Ask one short question in the current Director conversation. Link the original
thread or source. For example: “You asked this agent to keep the existing worker.
Was that because of current traffic, or something else?” Do not send the question
to the observed agent. Avoid asking for information already in the transcript.

Save the exact question before presenting it:

```sh
python3 director/learning.py ask --snapshot state/learning/<snapshot>.json \
  --source <message-source-id> --question "<exact question>"
python3 director/learning.py answer --id <question-id> --text "<exact answer>"
python3 director/learning.py dismiss --id <question-id> --text "<explicit dismissal>"
```

Use structured subprocess arguments for quotes or untrusted text. `ask` validates
that the source exists in the saved launch-app snapshot. bb questions require a
human message; cmux transcript questions retain their unverified attribution.
Only one question may be open per app. A source already asked about is not asked
again, including after dismissal. Check for equivalent questions from other
sources too; the script does not decide semantic equivalence. No answer means
pending, never assent. Continue independent observation and requested work.

## Learn without inventing policy

A snapshot is observed evidence. A possible explanation is an inference. An exact
answer is teaching within its stated context. These remain separate.

Record confirmed teaching in `profile/qa.md` with its source, exact words,
interpretation, reason, scope, circumstances, and ending condition. Reuse an
existing answer when it resolves the uncertainty. Do not ask for redundant
confirmation of an explicit preference already stated in context. Keep inferred
patterns tentative; ask before making them broader rules. If scope is unclear,
keep it within the source session. End or supersede old lessons explicitly.
See [memory.md](memory.md).

This adds no model service, background watcher, timer, or intervention permission.
The Director agent chooses useful questions during its learning turns; the script
collects evidence and keeps records. Starting recurring observation still requires
an explicit instruction, as it did before.
