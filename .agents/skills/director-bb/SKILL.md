---
name: director-bb
description: 'bb mechanics for the Director: identify itself, scan, read a stopped thread, re-check it, message it, approve or deny its prompt, and log. Read only when the Director was launched inside a bb thread (BB_THREAD_ID is set). Differentiator: the Director subset of bb-cli; nothing about spawning, layout, or plugins.'
---

# Director in bb

Your own thread is `$BB_THREAD_ID`. `bb status --json` shows it. If it is not titled DIRECTOR, ask the operator to rename it.

## Scan (read-only)

```bash
python3 director/scan.py            # every stopped bb thread on this Mac, ranked; saves state/scans/<time>.json
python3 director/scan.py --hours 6  # only threads updated in the last 6 hours
```

Per candidate: `id`, `title`, `project`, `provider`, `status` (`idle`, `error`, `pending`), `idle_min`, `pending_interaction`, `recent_error`, `ends_with_question`, `last_agent_msg`, `user_min_ago` (last human message), and the review fields `eligibility_reason`, `review_eligible`, `history`. `skipped_user_recent` lists threads the operator wrote to within the profile’s recent-human-input window. `coverage: partial` means some thread data or logs could not be read reliably; those failures are in `errors`, so do not treat affected threads as clear.

## Read one thread

```bash
bb thread show <id> --json                 # status, pending interaction, parent, environment
bb thread log <id> --all                   # whole conversation; use for a thread you have not handled
bb thread queue list <id> --json           # later instructions not yet in the conversation
bb thread log <id>                         # newest turns; enough for one you handled recently
bb thread interactions list <id> --json    # what it is waiting on: command, file change, plan, permission, question
bb thread output <id>                      # its latest final message
```

Read the queue even after `log --all`: queued instructions enter the conversation only when dispatched. Use their contents and order when establishing the current phase and authorization. Respect any schedule or waiting condition; queued work is not permission to bypass it or send a duplicate instruction. If the queue cannot be read, report the missing context and do not act.

Its repo: `bb thread show <id> --json` gives the environment; read that repo's `AGENTS.md` and `docs/adr/` before deciding.

## Re-check right before acting

```bash
bb thread show <id> --json    # status must still be stopped
bb thread queue list <id> --json  # compare with the queue read for the proposal
python3 director/scan.py               # the thread must not be in skipped_user_recent
```

The scan covers dispatched input only. Check queue creation and edit times against the profile’s recent-human-input window too. If the queue changed since the proposal or cannot be read, skip the approved action and review the new context.

The target must still be a candidate with `input_history_known: true`, and its current prompt must still match the approved action. If it is running, absent, recently contacted, unreadable, or changed, do nothing and append `outcome --status skipped` to the original review. Keep the original scan and run number; the recheck scan may no longer contain the target.

## Act (only after explicit approval)

```bash
bb thread tell <id> "<approved message in the profile’s style>"                        # steers immediately
bb thread interactions approve <interactionId> <id>                    # command, file change, or plan
bb thread interactions grant <interactionId> <id>                      # permission
bb thread interactions answer <interactionId> <id> --choice <questionId=value>   # or --text <questionId=text>
bb thread interactions deny <interactionId> <id>                       # then tell it to stay on mission
bb thread retry <id>                                                   # errored thread: re-sends its failed turn verbatim
```

A `tell` to a thread that is waiting on an interaction is queued until the interaction settles, so resolve the interaction first, or deny it and then tell. Use `--json` to read `delivery: sent|queued` and record that exact result. Queued is not a failure or proof of a resume; do not resend.

## Never

- Open, split, focus, stop, archive, hide, delete, or spawn threads. Do not `bb thread wait` or poll logs.
- Touch cmux. You watch bb only.
- Message a thread the operator wrote to within the profile’s recent-human-input window.

## Record the proposal and outcome

Before asking for approval, save the review and exact proposed action. For `leave`, omit `--proposed-action`.

```bash
python3 director/log.py run --picked <id> --title "..." --decision unblock|leave|wait_for_david|deny \
  --seen "..." --reason "..." [--proposed-action "<exact unsent action>"] [--rule Qnn] \
  --candidates N --skipped N --scan <scan path from scan.py>
```

Keep the returned run number. After approval and recheck, append what actually happened:

```bash
python3 director/log.py outcome --run N --status sent --detail "<actual delivery result>"
# Use queued, failed, or skipped when appropriate. --action records approved text that changed.
python3 director/log.py outcome --run N --status skipped --detail "<why>" --scan <recheck scan path>
python3 director/log.py confirm --run N   # once after sent/queued; reads bb, never sends input
```

`confirm` records whether the same thread is now `active`. A stopped or unreadable thread stays unconfirmed; do not poll or resend to force confirmation. New attempts need a new review. Then one short line with the actual outcome.
