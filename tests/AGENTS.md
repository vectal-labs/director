# Tests

- From the repo root: `python3 -m unittest discover -s tests -p 'test_*.py'`.
- CLI fixtures mirror the real layout: scripts in `director/`, teaching in root `profile/` and operational data in root `state/`; legacy fixtures use `private/` only to verify migration. Copy scripts from the repo, never personal files.
- Use fake `bb` and `cmux` binaries. Never message real agents or change real app state during tests.
