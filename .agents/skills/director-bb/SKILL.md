---
name: director-bb
description: 'bb mechanics for the Director: identify itself, scan, read a stopped thread, re-check it, message it, approve or deny its prompt, and log. Read only when the Director was launched inside a bb thread (BB_THREAD_ID is set). Differentiator: the Director subset of bb-cli; nothing about spawning, layout, or plugins.'
---

# Director in bb

Your own thread is `$BB_THREAD_ID`. `bb status --json` shows it. If it is not titled DIRECTOR, ask the operator to rename it.

## Scan (read-only)

```bash
python3 scan.py            # every stopped bb thread on this Mac, ranked; saves private/scans/<time>.json
python3 scan.py --hours 6  # only threads updated in the last 6 hours
```

Per candidate: `id`, `title`, `project`, `provider`, `status` (`idle`, `error`, `pending`), `idle_min`, `pending_interaction`, `recent_error`, `ends_with_question`, `last_agent_msg`, `user_min_ago` (last human message), and the review fields `eligibility_reason`, `review_eligible`, `history`. `skipped_user_recent` lists threads the operator wrote to in the last 3 minutes. `coverage: partial` means some `bb thread log` calls failed; those threads are in `errors`, so do not treat them as clear.

## Read one thread

```bash
bb thread show <id> --json                 # status, pending interaction, parent, environment
bb thread log <id> --all                   # whole conversation; use for a thread you have not handled
bb thread log <id>                         # newest turns; enough for one you handled recently
bb thread interactions list <id> --json    # what it is waiting on: command, file change, plan, permission, question
bb thread output <id>                      # its latest final message
```

Its repo: `bb thread show <id> --json` gives the environment; read that repo's `AGENTS.md` and `docs/adr/` before deciding.

## Re-check right before acting

```bash
bb thread show <id> --json    # status must still be stopped
python3 scan.py               # the thread must not be in skipped_user_recent
```

If it is running or the operator wrote within 3 minutes, do nothing and log `leave` with the reason.

## Act (manual stage: only after the operator says yes)

```bash
bb thread tell <id> "YOUR MESSAGE IN FULL CAPS"                        # steers immediately
bb thread interactions approve <interactionId> <id>                    # command, file change, or plan
bb thread interactions grant <interactionId> <id>                      # permission
bb thread interactions answer <interactionId> <id> --choice <questionId=value>   # or --text <questionId=text>
bb thread interactions deny <interactionId> <id>                       # then tell it to stay on mission
bb thread retry <id>                                                   # errored thread: re-sends its failed turn verbatim
```

A `tell` to a thread that is waiting on an interaction is queued until the interaction settles, so resolve the interaction first, or deny it and then tell. `--json` shows `delivery: sent|queued`; queued is not a failure, do not resend.

## Never

- Open, split, focus, stop, archive, hide, delete, or spawn threads. Do not `bb thread wait` or poll logs.
- Touch cmux. You watch bb only.
- Message a thread the operator wrote to in the last 3 minutes.

## Log

```bash
python3 log.py run --picked <id> --title "..." --decision unblock|leave|wait_for_david|deny \
  --seen "..." --reason "..." [--action "<message actually sent>"] [--rule Qnn] \
  --candidates N --skipped N --scan <scan path from scan.py>
```

Then one short line in your own thread: what you reviewed, what you decided, why.
