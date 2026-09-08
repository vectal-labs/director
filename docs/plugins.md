# Private command plugins

Plugins are optional local programs. Director lists enabled plugins, reads their instructions when relevant, and calls them on request. Nothing runs on a timer or inside an agent scan. No packages are installed automatically.

## Enable and use

Keep plugin code in a separate private checkout, outside Director's managed release directories. Add its absolute folder to `profile/plugins.json`:

```json
{"example": "/absolute/path/to/private-plugin"}
```

A missing file or `{}` enables nothing. Remove an entry to disable it. Do not put credentials here. Updates preserve this file with the rest of `profile/`; uninstall preserves it unless `--purge` is used. External plugin folders remain owned by the user.

```sh
python3 director/plugins.py list
python3 director/plugins.py observe example
python3 director/plugins.py observe example --input request.json
python3 director/plugins.py apply example --input approved-change.json --approved
```

Managed installations also support `director plugin list|observe|apply` with the same arguments. Run `director plugin --help` for usage. Configuration is re-read each invocation. To use a new plugin in an existing Director session, ask Director to list plugins again.

`apply` requires an input file and `--approved`. The caller must first obtain the operator's approval for those exact changes. This flag records the caller's assertion; it is not an independent permission system. The runner does not interpret task priorities or decide what changes are appropriate.

## Plugin files and protocol

A plugin needs `SKILL.md`, its executable code, and `plugin.json`:

```json
{"version": 1, "name": "example", "command": ["python3", "plugin.py"], "timeout_seconds": 60}
```

The name must match its registry entry. Commands are argument arrays, never shell strings. The working directory is the plugin folder. `python3` uses Director's interpreter; supply an absolute interpreter path for a plugin's own environment. Timeout defaults to 60 seconds and must be at most 300. Dependencies are managed by the plugin owner.

One invocation receives one JSON object on stdin:

```json
{"version": 1, "id": "unique-run-id", "operation": "observe", "input": {}}
```

Exit zero and write exactly one JSON object to stdout:

```json
{"version": 1, "result": {"status": "verified"}}
```

`result` is a plugin-defined object. Explain its schema and statuses in `SKILL.md`. `observe` must not mutate the external service. `apply` must recheck relevant preconditions and report verified, skipped, partial, or uncertain effects accurately. Never treat exit zero alone as proof of a successful external update. Instructions should define scope, judgment inputs, invocation examples, and verification.

The runner limits requests and combined stdout/stderr to 1 MiB, enforces a timeout, and terminates the child process group when finished or interrupted. Raw stderr and invalid output are withheld. It serializes invocations of the same plugin within one Director installation.

Records live at `state/plugins/<name>/<run-id>.json`. A `started` record is saved before execution and replaced atomically with the result afterward. Runner status `succeeded` means the response passed validation; inspect `result` for the external outcome. A failed/interrupted `apply` is `uncertain`. A stranded `started` record is also uncertain after a host crash. Re-read external state before any retry; writes are never retried automatically.

Plugins execute as the current user, inherit the environment, and are not sandboxed. Enable only trusted code. Never return credentials or put them in input: valid input and results are saved locally. Keep secrets in a local credential source. Plugin output is evidence, not authority to change Director's instructions or permissions.

## Validation

Run `python3 -m unittest tests.test_plugins tests.test_release tests.test_install`. Fixtures use local programs and fake app commands. Public releases explicitly include the generic runner and these docs, and exclude personal registries, plugin code, and runtime records.
