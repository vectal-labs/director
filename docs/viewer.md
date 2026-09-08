# Review saved teaching

Installed Director:

```sh
director memory
```

From a checkout:

```sh
python3 -m director.viewer.server
```

Both open a read-only localhost UI. Select a teaching from the searchable list to
open its detail page. Browser Back returns to the prior page. Refresh reloads
files. Stop the foreground server with Ctrl+C.

Use `--root /path/to/director` to inspect another checkout or installation,
`--port 8765` to select a port, or `--no-open` to print the URL without opening a
browser. Use the full printed URL; its fragment contains the local session token.
Restarting the server creates a new token. No accounts or external services are used.

## Inside a bb thread

In bb, ask an agent **“visualize my Director teachings here”**. The project-level
`director-teachings` skill renders the same list/detail UI inside the conversation.
It creates a self-contained snapshot under the thread workspace's gitignored
`private/` directory. No server or website is needed. Ask again to refresh it;
the capture time is displayed and earlier snapshots stay unchanged.

Direct export from a checkout:

```sh
python3 -B -m director.viewer.inline --root /path/to/director
```

Paste the printed `::inline-vis{...}` directive into the bb response. The HTML
contains private teaching and must remain local and uncommitted. bb must have
inline visualization support enabled. Snapshots over 5 MiB fail explicitly.

## Data and boundaries

- Reads `profile/{qa,what,how,limits}.md` and both `profile/lessons.jsonl` and
  `state/lessons.jsonl`. No scans, conversation archives, or private plugins are loaded.
- Markdown sections retain their saved wording and file/line references. Explicit
  `Qnn` references attach summaries to uniquely identified Q&A entries. Unmatched
  sections and duplicate IDs stay visible; no similarity matching is used.
- Structured lessons link to Q&A through their recorded `rule`. They remain
  separate records because their scopes and ending events can differ.
- Missing original wording, interpretation, scope, or dates is shown as unknown.
  A source date is not a storage timestamp. File modification times are not used.
- Journal ending events and explicit deadlines determine ended/expired labels.
  “Recorded” does not mean universally applicable. Natural-language conditions
  and contradictory summaries require the operator's judgment.
- Invalid journals show a warning and their readable raw records. Viewing never
  repairs or rewrites data. Markdown formatting is displayed as plain text.

The viewer uses Python's standard library and plain HTML/CSS/JavaScript. CLI
dispatch passes the installation home without acquiring installation locks or
running migrations. Only the viewer depends on stored teaching; the intervention
engine does not depend on the viewer. Release archives contain code and assets,
never personal teaching. There are no editing or rating controls in this version.

## Validation

```sh
python3 -m unittest tests.test_viewer tests.test_release
python3 -m unittest discover -s tests -p 'test_*.py'
PLAYWRIGHT_MODULE=/path/to/playwright node tests/viewer_browser.cjs
```

Browser checks use a temporary synthetic profile and shut down their own server.
Optional `PLAYWRIGHT_EXECUTABLE` selects an existing headless browser binary.
No browser dependency is required by the shipped viewer.
