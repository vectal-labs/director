#!/usr/bin/env python3
"""Check prerequisites and create missing personal profile files. Safe to run again."""
import argparse
import json
import shutil
import sys


try:
    from .storage import ROOT
    from .migrate import migrate
    from .preferences import DEFAULTS, read as read_preferences
except ImportError:
    from storage import ROOT
    from migrate import migrate
    from preferences import DEFAULTS, read as read_preferences

TEMPLATE_FILES = ("what.md", "how.md", "limits.md", "qa.md")


def read_templates(root=ROOT):
    """Validate every public starter before creating or migrating personal data."""
    for directory in (root / "templates", root / "templates/profile"):
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError(f"Missing or invalid public profile template directory: {directory}")
    templates = {}
    for name in TEMPLATE_FILES:
        path = root / "templates/profile" / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Missing or invalid public profile template: {path}")
        content = path.read_bytes()
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError(f"Public profile template must be UTF-8 Markdown: {path}") from None
        if not text.strip() or "\x00" in text:
            raise ValueError(f"Public profile template must contain nonempty Markdown: {path}")
        templates[name] = content
    return templates


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True, choices=("bb", "cmux"), help="the app you will use")
    parser.add_argument("--quiet", action="store_true", help="suppress instructions when called by the installer")
    args = parser.parse_args()
    say = (lambda *a, **kw: None) if args.quiet else print
    if sys.platform != "darwin":
        parser.exit(1, "This setup supports macOS.\n")
    if sys.version_info < (3, 9):
        parser.exit(1, "Python 3.9 or newer is required. Check with python3 --version.\n")

    if args.app == "cmux":
        from cmux_app import cmux_bin
        binary = cmux_bin()
    else:
        binary = "bb"
    if not shutil.which(binary):
        hint = ("Install cmux in /Applications or add its CLI to PATH." if args.app == "cmux"
                else "Install bb and add its CLI to PATH.")
        parser.exit(1, f"Missing {args.app} CLI. {hint} Then run setup again.\n")
    say(f"Requirements OK: macOS, Python {sys.version.split()[0]}, {args.app} CLI.")

    try:
        templates = read_templates()
        migrate(ROOT)
        (ROOT / "state").mkdir(parents=True, exist_ok=True)
        judgment = ROOT / "profile"
        judgment.mkdir(parents=True, exist_ok=True)
        for name, content in templates.items():
            path = judgment / name
            try:
                with path.open("xb") as file:
                    file.write(content)
                say(f"Created profile/{name}")
            except FileExistsError:
                if not path.is_file():
                    raise ValueError(f"profile/{name} must be a file")
                say(f"Kept profile/{name}")
        settings = judgment / "settings.json"
        try:
            with settings.open("x", encoding="utf-8") as file:
                json.dump(DEFAULTS, file, indent=2)
                file.write("\n")
            say("Created profile/settings.json")
        except FileExistsError:
            read_preferences(settings)
            say("Kept profile/settings.json")
    except (OSError, ValueError) as error:
        parser.exit(1, f"Could not set up rule files: {error}\n")

    say("\nRead and edit the rules in profile/ before starting.")
    if args.app == "cmux":
        say("Run cmux hooks setup once so your agents report their state.")
    say(f"Open this repo in {args.app} and tell your agent: Read ROLE.md and be the Director.")


if __name__ == "__main__":
    main()
