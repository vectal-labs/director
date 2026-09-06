# Runtime

- Run commands from the repo root: `python3 director/setup.py`, `python3 director/scan.py`, and `python3 director/log.py`.
- Rules, logs, and scans stay in root `private/`, resolved from the script path. Keep legacy `scans/<name>.json` references readable there.
- Keep scans and confirmation read-only and confined to the launch app. Action instructions live in the bundled app skills; the role stays in root `ROLE.md`.
