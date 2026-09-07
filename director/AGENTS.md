# Runtime

- Run commands from the repo root: `python3 director/setup.py`, `python3 director/scan.py`, and `python3 director/log.py`.
- Rules and preferences live in root `profile/`; logs, scans, and launch state live in root `state/`. Use `storage.py` for paths and `migrate.py` for legacy data. Preserve old scan references and history.
- Keep scans and confirmation read-only and confined to the launch app. Action instructions live in the bundled app skills; the role stays in root `ROLE.md`.
