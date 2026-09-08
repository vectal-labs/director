# Personal profile and runtime state

The shared repository contains Director's code, role, app skills, and documentation.
Public starter rules live in tracked `templates/profile/`. Operator teaching and
preferences live in `profile/`. Operational data lives in
`state/`. Both directories are gitignored and excluded from release archives.
`private/` is unrelated private storage; Director does not load it as a profile.

## File ownership

```text
templates/profile/  Public what.md, how.md, limits.md, and qa.md starters
profile/
  what.md          Scope and judgment
  how.md           Communication style and review workflow
  limits.md        Personal limits
  qa.md            Exact teaching and corrections, with sources and scope
  settings.json    Numeric behavior settings and legacy correction mappings
  priorities.json Personal project and thread weights (optional)
  lessons.jsonl    Durable structured preferences and project decisions
state/
  log.jsonl        Original reviews, corrections, and delivery outcomes
  scans/           Saved observations
  lessons.jsonl    Temporary instructions and individual exceptions
  config.json     This installation's app, provider, model, and local IDs
  launches.json   Sessions owned by this installation
  migration-backup/  Original data saved before migration
```

Setup validates the four public templates and copies only missing Markdown files
into `profile/`. It creates missing settings from the defaults in
`director/preferences.py`. It never replaces an existing profile. Runtime creates
journals and state files when needed. App,
provider, and model selection remain installation configuration in this version;
copying a profile does not choose a different provider or launch a session.

`ROLE.md` defines the review and approval process. The app skills define commands.
The profile supplies personal judgment, presentation, and workflow. Profiles never
grant permission for new interventions. Manual approval remains required, and
recurring automation needs a separate explicit instruction.

## Develop while using your own Director

Edit shared code and `templates/profile/` to improve the public product. Edit
`profile/` when teaching your own Director. Both can live in the same checkout:
the root profile, state, and private directories are gitignored; the public
templates are versioned and included in releases.

Template edits affect fresh profiles and files created by a later setup. They do
not silently update existing teaching. Setup reports missing, empty, invalid
UTF-8, or linked template files before changing personal data.

Use temporary profiles in tests to check public defaults. The test suite copies
public templates into isolated directories and uses fake app commands. It never
needs your real profile or review history.

For a source checkout, create missing starter files with:

```sh
python3 director/setup.py --app bb  # or --app cmux
```

For managed installations, use `director setup --no-start`. Installed templates
belong to the release; updates replace them while preserving the personal profile.

## Settings

`profile/settings.json` accepts these optional fields; omitted fields use defaults:

```json
{
  "recheck_seconds": 3600,
  "recent_user_seconds": 180,
  "spot_check_chance": 0.0,
  "legacy_override_decisions": {}
}
```

Times must be finite, nonnegative numbers. Sampling chance must be between 0 and 1.
The legacy mapping supplies corrected decisions for older overrides that omitted
their decision field. It is personal data, not a shared list of numbered lessons.
Unknown or invalid fields stop the command instead of silently changing behavior.
Each scan records the resolved settings and priorities for later inspection.

## Migration

From a source checkout, run:

```sh
python3 director/migrate.py
```

Setup, scans, log commands, and the installed launcher also migrate older layouts.
Only known Director files move: the 4 judgment files and priorities go to
`profile/`; logs, scans, and launcher records go to `state/`. Other private files
stay where they are. Existing teaching is preserved byte for byte. Migration
extracts old code settings when available without executing the old code.

Migration checks conflicting destinations before moving data and saves originals
under `state/migration-backup/`. It refuses to choose between differing copies.
It is safe to rerun after completion or an interrupted move. Legacy paths remain
links to the canonical data; old scan references stay readable. Old Director log
writers retain the same file and lock. Run migrations between scan commands;
use the current installed launcher for management commands after an upgrade.

Structured lessons are copied into the appropriate journal with their original
words, IDs, scope, timestamps, and ending evidence. Legacy corrections without an
explicit lesson stay historical evidence. They are never promoted automatically.
See [memory.md](memory.md) for recording and ending lessons.

## Portability and backup

`profile/` can be copied to a fresh compatible Director checkout without its old
review log. Its reusable lessons remain usable and can still be ended. Project
and thread IDs keep their original scope; copying does not remap or generalize
them. Review personal references before sharing a profile.

For a full backup, retain `profile/`, `state/`, and the matching code version.
GitHub does not back up either personal directory. Restoring operational state
also restores installation-specific references and requires the corresponding
environment. There is no preset switcher or export/import command yet.
