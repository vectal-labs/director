---
name: director-teachings
description: Show Director's saved lessons and teachings as an interactive view directly inside a bb thread. Use for "visualize my teachings", "show what Director learned", "review my lessons", or requests for the learning viewer in chat. For reviewing saved memory, not starting Director or changing its teaching.
---

# Director teachings

Render the existing viewer inside the current bb thread. Keep the full lesson
list and individual lesson detail on separate screens.

1. Locate this Director checkout at `../../..` from this skill's directory.
   Choose the profile root explicitly: use the requested checkout/installation,
   otherwise the current Director checkout. If working in an empty worktree,
   locate the primary checkout with `git worktree list`; do not present its empty
   profile as the operator's memory. Do not copy personal teaching into Git.
2. From the **current thread workspace**, run the exporter with that code on the
   Python module path. Replace the paths with the resolved locations:

   ```sh
   PYTHONPATH=/path/to/director python3 -B -m director.viewer.inline --root /path/to/profile-root
   ```

   It writes a unique, private HTML snapshot under the current workspace's
   `private/` directory. Ensure that directory is gitignored before exporting.
   For a refresh, run it again; the previous snapshot remains unchanged.
3. Confirm the command succeeded and the printed HTML path exists. Return the
   **exact printed `::inline-vis{...}` directive** on its own line, outside code
   fences, with at most one short sentence. Do not substitute a website link,
   launch a server, open a browser, or summarize away the interactive view.

The snapshot reuses Director's reader and UI. It performs no network calls and
does not change teaching. Missing dates and interpretations remain unknown.
All content stays local, but the HTML contains personal teaching; never commit
or publish it. If rendering fails or bb cannot display the directive, explain
the actual error without claiming the viewer appeared.
