#!/usr/bin/env python3
"""Check prerequisites and create missing personal profile files. Safe to run again."""
import argparse
import json
import shutil
import sys


from storage import ROOT
from migrate import migrate
from preferences import DEFAULTS, read as read_preferences
EXAMPLES = {
    "what.md": """# What to do

- Keep my coding agents moving on work I have already requested.
- Leave finished agents alone.
- Ask me when a real product, design, or architecture decision is needed.
""",
    "how.md": """# How to work

- Review 1 stopped agent when I ask.
- Show the exact proposed action and your reason before asking for approval.
- Keep replies short and clear.
- Keep corrections scoped: general preference, project decision, temporary instruction, or exception. See ROLE.md and docs/memory.md.
""",
    "limits.md": """# Limits

- Ask me before every message, approval, denial, or retry.
- Do not approve anything irreversible or very costly.
- Do not start recurring scans unless I explicitly ask.
""",
    "qa.md": """# Questions and corrections

No answers yet. Append each question and my exact answer as Q01, Q02, and so on.
Keep the agent's interpretation, reason, scope, applicable situation, and any ending condition separate from my words. Missing context is unknown, not permission to generalize.
""",
}


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
        migrate(ROOT)
        (ROOT / "state").mkdir(parents=True, exist_ok=True)
        judgment = ROOT / "profile"
        judgment.mkdir(parents=True, exist_ok=True)
        for name, text in EXAMPLES.items():
            path = judgment / name
            try:
                with path.open("x", encoding="utf-8") as file:
                    file.write(text)
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
