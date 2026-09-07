# Director

Director helps keep your coding agents moving in bb or cmux. It reviews stopped agents and asks for your approval before taking action.

## Quick start

**Requires:** macOS, Python 3.9+, and bb or cmux with an authenticated agent provider. No Python packages to install.

```sh
curl -fsSL https://raw.githubusercontent.com/vectal-labs/director/main/install.sh | sh
```

Setup chooses your app and agent, creates starter rules, and starts Director. Your provider may ask you to trust the folder or sign in. Review the rules in `~/.local/share/director/profile/` before approving actions.

```sh
director start
director update
director uninstall          # keep rules and history
director uninstall --purge  # also remove personal data
```

For cmux, run `cmux hooks setup` so watched agents report their state. Say **“scan”** or **“next”** when you want a review. Recurring scans require a separate instruction.

See [installation options and removal](docs/install.md). To work from source, clone this repo, run `python3 director/setup.py --app bb` (or `cmux`), then tell your agent: `Read ROLE.md and be the Director.`

## How it behaves

- **You approve actions.** Director shows its proposed message or prompt response before sending it.
- **One app at a time.** It watches only the app it was launched in.
- **Your rules stay local.** Teaching is saved in gitignored `profile/`; reviews and runtime history are saved in gitignored `state/`. Each teammate keeps their own history.

## Documentation

[Director’s role](ROLE.md) · [Technical reference](docs/reference.md) · [Migration history](docs/migration.md)
