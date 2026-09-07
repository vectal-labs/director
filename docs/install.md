# Install Director

Director requires macOS, Python 3.9+, and bb or cmux with a working agent provider. Sign in to the provider first.

```sh
curl -fsSL https://raw.githubusercontent.com/vectal-labs/director/main/install.sh | sh
```

Setup asks which app, provider, and model to use. It creates missing rules and starts Director with its role loaded. The provider may show its normal folder-trust or sign-in prompt. Review the starter rules before approving Director's first action.

If Python is missing, install Python 3.9 or newer and rerun the installer. Setup does not install bb, cmux, or provider credentials.

## Commands

```sh
director start                # reopen using saved settings
director setup                # change settings, then start
director setup --no-start     # change settings only
director update               # install the latest stable release
director update --version v0.1.0
director --version
```

`start` reuses an existing Director session. After an update, close that session and run `director start` to use the new release.

Pass explicit installer options as arguments to `sh`:

```sh
curl -fsSL https://raw.githubusercontent.com/vectal-labs/director/main/install.sh | sh -s -- --app bb --provider codex --model MODEL_ID --yes
```

Replace `MODEL_ID` with an available model. `--yes` uses supplied or saved settings without questions. Use `--no-start` to configure without launching, or `--no-setup` to install files only. Put `--version v0.1.0` before setup options to select a release.

For cmux, the initial launcher supports Claude Code and Codex. Run `cmux hooks setup` for agents you want Director to see. These are shared cmux settings and are managed separately.

## Files and updates

- `~/.local/bin/director`: the command launcher.
- `~/.local/share/director/private/`: rules, configuration, launch ownership, logs, and scans.
- `~/.local/share/director/releases/`: installed program versions.
- `~/.local/share/director/current`: the active release link.
- `~/.local/share/director/install.json`: installed file and shell-change ownership.

The install folder provides stable links to the role, scripts, and skills. This lets bb keep the same workspace and write private data across updates. Set `DIRECTOR_HOME` before installation to use a different, empty folder.

The installer adds a marked PATH block to `.zshrc` or `.bash_profile` when needed. Open a new terminal afterward, or use `~/.local/bin/director` immediately. Symlinked profiles and other shells receive manual PATH instructions.

Downloads come from GitHub Releases over HTTPS. SHA256 checksums detect damaged or mismatched downloads; they are not a signature from a separate trust authority. Updates stage a complete release before switching `current`. Old releases remain available to existing sessions. Personal files are preserved.

Existing source checkouts are independent. This installer does not migrate or delete their private data. Copy your rules and history into the installed private folder yourself if needed.

## Uninstall

```sh
director uninstall           # keep personal rules and history
director uninstall --purge   # also delete personal rules and history
```

Uninstall stops only sessions recorded by this installation. It verifies their app metadata before acting. It removes its launcher, release files, workspace links, and marked PATH changes. If a session cannot be verified or stopped, open the app and resolve the reported issue, then retry. Changed launcher files, workspace links, or release contents are preserved with an error.

With `--purge`, Director also removes its owned bb threads and empty project registration. Other threads in that project must be moved first. Shared apps, cmux hooks, provider authentication, provider chat history, and independently scheduled automations remain under their original owners.

To delete retained personal data after a normal uninstall:

```sh
curl -fsSL https://raw.githubusercontent.com/vectal-labs/director/main/install.sh | sh -s -- --uninstall --purge
```

## Release verification

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
sh -n install.sh
python3 scripts/build_release.py --version v0.1.0 --output /tmp/director-release-v0.1.0
```

The output directory must be new. It contains `director-v0.1.0.tar.gz`, `SHA256SUMS`, and `install.sh`. The builder uses an explicit file allowlist. It excludes personal data, credentials, tests, and Git state.

Pushing a stable `vN.N.N` tag triggers the macOS test and release workflow. The workflow publishes those assets after tests pass. Never replace assets under an existing version; publish a new version instead.

For local release tests only, `DIRECTOR_RELEASE_BASE_URL` can point to a localhost HTTP directory containing those assets. Production release URLs require HTTPS.
