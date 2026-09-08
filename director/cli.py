#!/usr/bin/env python3
"""Install, configure, start, update, and remove Director."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

# The downloaded bundle must remain unchanged until release validation finishes.
sys.dont_write_bytecode = True

import install
import launch
import lifecycle


def configure(root, args):
    install.read_templates((root / "current").resolve())
    install.migrate(root)
    state = root / "state"
    workspace = root
    config = launch.configure(args.app, args.provider, args.model, workspace, state, interactive=not args.yes)
    subprocess.run([sys.executable, str(workspace / "director/setup.py"), "--app", config["app"], "--quiet"], check=True)
    install.save(state / "config.json", config)
    return config


def config_args(parser):
    parser.add_argument("--app", choices=("bb", "cmux"))
    parser.add_argument("--provider", help="bb provider ID, or claude/codex for cmux")
    parser.add_argument("--model", help="model ID; omit to choose interactively")
    parser.add_argument("--yes", action="store_true", help="use supplied or saved settings without interactive questions")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=install.home(), help=argparse.SUPPRESS)
    parser.add_argument("--version", action="store_true", help="show installed version")
    sub = parser.add_subparsers(dest="command")
    add = sub.add_parser("install", help="install a verified release (normally called by install.sh)")
    add.add_argument("--source", required=True, type=Path)
    add.add_argument("--no-start", action="store_true", help="configure without launching an agent")
    add.add_argument("--no-setup", action="store_true", help="install files only; run director setup later")
    config_args(add)
    setup = sub.add_parser("setup", help="configure the app and agent")
    setup.add_argument("--no-start", action="store_true")
    config_args(setup)
    start = sub.add_parser("start", help="start Director with saved settings")
    config_args(start)
    update = sub.add_parser("update", help="install a newer release, preserving personal data")
    update.add_argument("--version", dest="release_version", help="explicitly install v1.2.3")
    remove = sub.add_parser("uninstall", help="stop owned sessions and remove Director")
    remove.add_argument("--purge", action="store_true", help="also permanently delete personal rules, logs, and scans")
    args = parser.parse_args(argv)
    root = args.home.expanduser().absolute()
    if args.version:
        print(install.metadata(root).get("version", "not installed"))
        return 0
    if not args.command:
        parser.print_help()
        return 0
    if sys.platform != "darwin" or sys.version_info < (3, 9):
        parser.error("Director requires macOS and Python 3.9 or newer.")
    try:
        with install.locked(root, create=args.command == "install"):
            if args.command == "install":
                old = install.metadata(root) if (root / "install.json").exists() else None
                install.check_launcher(old)
                data = install.activate(root, args.source.resolve(), old)
                binary = install.install_launcher(root, data)
                install.setup_path(root, data)
                print(f"Installed Director {data['version']} at {root}.")
                if args.no_setup:
                    print(f"Next: {binary} setup")
                else:
                    config = configure(root, args)
                    if not args.no_start:
                        launch.start(config, root, root / "state")
            elif args.command == "setup":
                install.metadata(root)
                config = configure(root, args)
                if not args.no_start:
                    launch.start(config, root, root / "state")
            elif args.command == "start":
                install.metadata(root)
                install.migrate(root)
                path = root / "state/config.json"
                if path.exists() and not any((args.app, args.provider, args.model)):
                    config = json.loads(path.read_text())
                else:
                    config = configure(root, args)
                launch.start(config, root, root / "state")
            elif args.command == "update":
                lifecycle.update(root, args.release_version)
            elif args.command == "uninstall":
                lifecycle.uninstall(root, args.purge)
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Director: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nDirector cancelled. Existing personal data was kept.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
