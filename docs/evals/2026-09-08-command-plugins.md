# Private command plugins: validation

The implementation adds an opt-in registry, one subprocess runner, role instructions, and installed CLI access. Scans and app adapters are unchanged. Default installations enable no plugins.

## Evidence and checks

- New process fixtures cover discovery without execution, disabled plugins, JSON round trips, approval/input requirements, literal command arguments, malformed configuration and responses, output limits, crashes, timeouts, descendant cleanup, SIGTERM, and concurrent invocation rejection.
- Installed-release tests exercise plugin discovery and execution, update preservation, shared state paths, and uninstall preservation of the registry and external plugin folder.
- Archive tests verify inclusion of the generic runner and exclusion of private plugin files, registries, and records.
- The final integrated suite passed all 228 tests with terminal access for the existing controlling-terminal fixture. Shell syntax passed. No tests were skipped or weakened.
- Independent review found cancellation cleanup, ambient curl settings in a private integration, and CLI help forwarding issues. Each was fixed and regression-tested. Private integration fixtures and live read measurements remain outside the public repository.

## Sources and limits

The boundary follows [Pi's optional package model](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/packages.md) and uses [Python subprocess process/session APIs](https://docs.python.org/3/library/subprocess.html). DeepAPI research requests completed: `f964cea6-6d74-465f-bae3-de59dcdfaf83` (protocol/process lifecycle), `6c86070b-c6b6-4f08-8484-194a269dca53` (private configuration/workflow), with earlier Pi and DeepSeek architecture research recorded in the implementation conversation.

The runner is a trusted local execution interface, not a sandbox. `--approved` records the caller's assertion of human approval. It does not independently authenticate a human. A host crash or failed apply can leave external effects uncertain; no write is automatically retried. No scheduler, marketplace, or package manager was added.
