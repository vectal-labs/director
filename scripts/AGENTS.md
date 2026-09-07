# Release tools

- `python3 scripts/build_release.py --version v0.1.0 --output /tmp/director-release` builds local assets without publishing.
- Keep release contents explicitly allowlisted. Never package personal state, credentials, checkout metadata, or tests.
- Archives must be reproducible and contain only relative, contained paths. Installed code and bundled instructions belong under `director/` in the archive.
- Verify packaging with `python3 -m unittest tests.test_release`.
