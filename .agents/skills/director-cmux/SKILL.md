---
name: director-cmux
description: 'cmux mechanics for the Director: identify itself, scan, read a stopped agent surface, re-check it, type a message or answer a prompt, and log. Read only when the Director was launched inside a cmux terminal (CMUX_SURFACE_ID is set). Differentiator: the Director subset of the cmux skill; no layout, browser, notification, or settings commands.'
---

# Director in cmux

Your own surface is `$CMUX_SURFACE_ID` in workspace `$CMUX_WORKSPACE_ID`; `cmux identify --json` confirms it. The scan excludes your surface.

## Ref rules

- Address surfaces by UUID (the scan's `id`) or `surface:N`. A bare number is an index and silently targets the wrong surface.
- `read-screen` takes `--surface`, never `--pane`. Without a target it reads your own screen.
- Never append `2>/dev/null`. cmux reports ref mistakes on stderr with exit 1.
- Only hook-integrated agents are visible (`cmux hooks setup`). If the operator's agents do not show up, tell them; do not guess.

## Scan (read-only)

```bash
python3 scan.py            # every stopped agent in open cmux workspaces, ranked; saves private/scans/<time>.json
python3 scan.py --hours 6  # only sessions updated in the last 6 hours
```

Sources: `cmux sessions list` (hook-recorded sessions), `cmux list-workspaces` (only open workspaces count), `~/.cmuxterm/events.jsonl` (last prompt per surface), and `cmux read-screen` per stopped agent. Per candidate: `id` (surface UUID), `title` (workspace and agent), `project` (cwd), `provider` (agent), `status` (`idle`, `needsInput`, `unknown`), `idle_min`, `pending_interaction` (`needsInput`), `last_agent_msg` (bottom of its screen), `user_min_ago`, and the review fields. `dropped` counts what was excluded and why; `coverage: partial` means some screens could not be read, listed in `errors`.

## Read one agent

```bash
cmux read-screen --surface <uuid> --scrollback --lines 300   # recent history; raise --lines for more
cmux sessions list --surface <uuid> --json                   # agent, cwd, lifecycle, transcript path
```

Read the agent's repo (`cwd`): its `AGENTS.md` and `docs/adr/` before deciding.

## Re-check right before acting

```bash
cmux read-screen --surface <uuid> --lines 20   # still stopped, still the same prompt?
python3 scan.py                                # the surface must not be in skipped_user_recent
```

If it is working or the operator wrote within 3 minutes, do nothing and log `leave` with the reason.

## Act (manual stage: only after the operator says yes)

```bash
cmux send --surface <uuid> "YOUR MESSAGE IN FULL CAPS"
cmux send-key --surface <uuid> enter
cmux read-screen --surface <uuid> --lines 20   # confirm the agent picked it up
```

- Look at the input line first. Claude Code may prefill a predicted draft; that draft is the agent, not the operator. Clear it before typing: `cmux send-key --surface <uuid> ctrl+u`, then read the screen again.
- Permission or choice prompts: answer with exactly what the screen offers, for example `cmux send-key --surface <uuid> enter` for the highlighted default, or `cmux send --surface <uuid> "2"` then `enter` for option 2. To deny, choose the "No" option, then send a message telling it to stay on mission.
- If the screen did not change, the input did not land. Read it again and report; do not spam keys.

## Never

- `select-workspace`, `focus-*`, `new-*`, `close-*`, `move-surface`, `notify`, `set-status`, `settings`, `hooks`, `browser`, or anything that changes layout, focus, or config.
- Send `ctrl+c`, `esc`, or keys that could interrupt a running agent.
- Touch bb. You watch cmux only.
- Type into a surface the operator wrote to in the last 3 minutes.

## Log

```bash
python3 log.py run --picked <uuid> --title "..." --decision unblock|leave|wait_for_david|deny \
  --seen "..." --reason "..." [--action "<message actually sent>"] [--rule Qnn] \
  --candidates N --skipped N --scan <scan path from scan.py>
```

Then one short line in your own terminal: what you reviewed, what you decided, why.
