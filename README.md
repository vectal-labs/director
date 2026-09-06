# Director

Director helps keep your coding agents moving in bb or cmux. It reviews stopped agents and asks for your approval before taking action.

## Quick start

**Requires:** macOS, Python 3.9+, Git, and either `bb` on your `PATH` or cmux installed in `/Applications` (or its CLI on your `PATH`). No Python packages to install.

**1. Install**

```bash
git clone https://github.com/vectal-labs/director.git ~/code/director
cd ~/code/director
python3 director/setup.py --app bb  # or --app cmux
```

<details>
<summary>Extra setup for cmux</summary>

Run this once so your agents report their state:

```bash
cmux hooks setup
```

</details>

**2. Set your rules**

Setup creates example rules in `private/judgment/`. Edit `what.md`, `how.md`, and `limits.md` to describe what Director should do, how it should work, and when it should ask you. Existing files are kept when you rerun setup.

**3. Start**

Use a frontier model. In bb, open a thread on this repo. In cmux, open a terminal in this repo and start your coding agent, such as `claude` or `codex`. Send:

```text
Read ROLE.md and be the Director.
```

Say **“scan”** or **“next”** whenever you want it to review an agent. Reviews are manual for now; recurring scans require a separate instruction.

## How it behaves

- **You approve actions.** Director shows its proposed message or prompt response before sending it.
- **One app at a time.** It watches only the app it was launched in.
- **Your rules stay local.** Reviews and corrections are saved in gitignored `private/`. Each teammate keeps their own history.

## Documentation

[Director’s role](ROLE.md) · [Technical reference](docs/reference.md) · [Migration history](docs/migration.md)
