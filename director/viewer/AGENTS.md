# Teaching viewer

- `reader.py` owns file formats and explicit record links; `server.py` owns loopback HTTP; browser files own presentation only.
- Accept the profile root explicitly. Installed code lives in an immutable release; personal data lives in the installation home.
- Reads must not migrate, reconcile, repair, write, scan, call app CLIs, or query a model. Reuse only read-only journal helpers.
- Preserve original records and unknown fields. Never infer approval, scope, storage timestamps, or whether a natural-language ending occurred.
- Serve only named assets and read-only API routes. Render stored text with `textContent`, never HTML. Keep host/origin checks and the session token.
- Tests: `python3 -m unittest tests.test_viewer`; browser checks: `node tests/viewer_browser.cjs` with `PLAYWRIGHT_MODULE` pointing to an installed Playwright module. See `docs/viewer.md`.
