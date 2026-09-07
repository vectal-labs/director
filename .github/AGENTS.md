# CI and releases

- Test with fake app CLIs on macOS. Workflows must never control real bb or cmux sessions.
- Version tags build tested, deterministic archives and checksums. Publishing requires only repository contents write permission in the release job.
- Never overwrite an existing release asset. A changed release needs a new version tag.
