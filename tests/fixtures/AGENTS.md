# Fixtures

- `legacy_install.py` is the exact `director/install.py` from commit `2a7fe053faaf051e0d51e851175787aa81b475b2`. Keep it unchanged. It reproduces upgrades activated by the installer that predates `profile/` and `state/`.
- Run fixtures only inside temporary test directories with fake apps. Never install into the real home or contact real agents.
