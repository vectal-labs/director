#!/usr/bin/env python3
"""Check prerequisites and create missing private rule files. Safe to run again."""
import argparse
import pathlib
import shutil
import sys


HERE = pathlib.Path(__file__).resolve().parent
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
""",
    "limits.md": """# Limits

- Ask me before every message, approval, denial, or retry.
- Do not approve anything irreversible or very costly.
- Do not start recurring scans unless I explicitly ask.
""",
    "qa.md": """# Questions and corrections

No answers yet. Append each question and my exact answer as Q01, Q02, and so on.
""",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True, choices=("bb", "cmux"), help="the app you will use")
    args = parser.parse_args()
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
    print(f"Requirements OK: macOS, Python {sys.version.split()[0]}, {args.app} CLI.")

    try:
        judgment = HERE / "private" / "judgment"
        judgment.mkdir(parents=True, exist_ok=True)
        for name, text in EXAMPLES.items():
            path = judgment / name
            try:
                with path.open("x", encoding="utf-8") as file:
                    file.write(text)
                print(f"Created private/judgment/{name}")
            except FileExistsError:
                if not path.is_file():
                    raise ValueError(f"private/judgment/{name} must be a file")
                print(f"Kept private/judgment/{name}")
    except (OSError, ValueError) as error:
        parser.exit(1, f"Could not set up rule files: {error}\n")

    print("\nRead and edit the rules in private/judgment/ before starting.")
    if args.app == "cmux":
        print("Run cmux hooks setup once so your agents report their state.")
    print(f"Open this repo in {args.app} and tell your agent: Read ROLE.md and be the Director.")


if __name__ == "__main__":
    main()
