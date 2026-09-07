# Installer validation: 2026-09-07

## Scope

One-command macOS installation, saved app/provider/model setup, launch, versioned updates, uninstall, purge, and deterministic release assets. Existing source-checkout scans, logs, and approval rules remain unchanged.

## Research

Completed 16 DeepAPI web searches, read 7 official documentation pages, and completed 3 follow-up DeepAPI research requests about installer behavior, macOS edge cases, and agent setup. Relevant primary references:

- [uv installation](https://docs.astral.sh/uv/getting-started/installation/): shell installer, version selection, PATH, updates, and removal.
- [Rust installation](https://rust-lang.org/install.html): guided shell installation and explicit self-uninstall.
- [Poetry installation](https://python-poetry.org/docs/): Python prerequisite, isolated installation, and removal.
- [Homebrew taps](https://docs.brew.sh/How-to-Create-and-Maintain-a-Tap): versioned distribution and dependency ownership.

These support the design choices. They do not establish Director's live launch behavior.

## Live capability checks

The installed bb CLI was version 0.42.1. The following were read-only checks against real CLIs or their installed source:

1. `bb status --json`: reports the execution host.
2. `bb project create --help`: supports an explicit local root and machine.
3. `bb project list --json`: sources include paths and host IDs.
4. `bb project show --json`: confirms the same source schema.
5. `bb provider list --machine ... --json`: reports availability and permission modes.
6. `bb provider models ... --json`: reports model IDs and defaults.
7. `bb thread spawn --help`: supports project, environment path, machine, provider, model, prompt, and normal permissions.
8. Installed bb source: machine selection can accompany an environment path; non-Git directories are accepted without running `git init`.
9. `bb thread show --json`: reports thread, environment path, and host.
10. `bb thread list --project ... --include-hidden --json`: archived threads need a separate query.
11. `bb thread history --json`: initial input text can verify interrupted-launch ownership.
12. A missing bb thread returns `Error: HTTP 404: Thread not found`; other errors are not treated as deletion.
13. `bb thread stop --help`: releases the agent runtime.
14. `bb thread delete --help`: deletion requires `--yes`; child deletion requires a separate flag that Director does not use.
15. `bb project delete --help`: deletes project data, so purge requires an owned, empty project with unchanged sources.
16. `cmux new-workspace --help`: supports name, cwd, and focus control.
17. `cmux close-workspace --help`: supports an explicit workspace.
18. `cmux tree --help`: supports all windows and JSON.
19. `cmux send --help`: supports explicit workspace/surface targeting and the argument separator.
20. `cmux hooks --help`: setup changes shared agent configuration; Claude wrapper hooks are automatic.
21. Claude and Codex help: confirms model, extra-directory, and normal permission flags.

cmux's live socket was offline. No real agent was launched, messaged, stopped, or deleted during verification. bb runtime skill caches and provider session data remain app-owned.

## Automated checks

The suite runs real shell installers and installed commands against HTTP fixture release servers and fake app CLIs. Temporary home directories include spaces. A real pseudo-terminal verifies that setup reads `/dev/tty` while stdin is a pipe. This test requires terminal access outside the agent sandbox.

Coverage includes fresh installation, repeated installation, guided and explicit setup, duplicate prevention, correct launch scope, matching ownership, ordinary uninstall, purge, retained history, malformed archives, path traversal, unexpected symlinks, checksum failures, HTTPS redirect policy, interrupted copying and removal, atomic launcher replacement, interpreter upgrades, bytecode caching, and invalid Python releases. Existing runtime tests remain in the suite.

Release tests verify an explicit source allowlist, absence of private files, exact symlink targets, repeatable archive bytes, checksums, and refusal to overwrite a version's output directory.

The final full suite passed **130 tests in 41.7 seconds** on Apple Python 3.9. Homebrew Python 3.14 also passed the 25 installation/recovery tests available at that check, then all 11 final recovery tests after the last compatibility fixes. Shell syntax and release checksums passed. The release workflow runs the suite on a fresh macOS CI runner with Python 3.11 when a version tag is published.

## Limits

Fixture tests establish command and file behavior. They do not prove a successful authenticated agent launch on a separate user's Mac. That live acceptance check remains to be performed. Normal provider sign-in and folder-trust prompts remain possible.

The repository was private and had no releases when publishing was inspected. A public download location requires the owner's visibility decision before the documented public command can work.
