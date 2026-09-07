# Director

- The system prompt is `ROLE.md`. App mechanics live in `.agents/skills/director-bb/` and `director-cmux/`. Keep the prompt about the role and the skills about their app.
- Director watches only the app it was launched in. `director/scan.py` detects it from `BB_THREAD_ID` or `CMUX_SURFACE_ID`; `director/bb_app.py` reads bb only, `director/cmux_app.py` reads cmux only. Keep it that way and keep the tests that prove it. See `director/AGENTS.md` for runtime contracts.
- Keep shared code and instructions independent of operator preferences. Personal teaching and policy belong in gitignored `profile/`; operational history belongs in gitignored `state/`. `private/` is unrelated private storage. See `docs/profile.md` for ownership and migration.
- Preserve manual approval for agent messages and disabled random checks by default. `director/memory.py` keeps the dormant sampler with weights and seed so old scans replay.
- Preserve historical logs and scans. Old records without a state snapshot stay eligible until reviewed with a new scan. Cooldown starts at the last recorded review; scanning alone never resets it.
- Test: `python3 -m unittest discover -s tests -p 'test_*.py'`. CLI tests use fake `bb` and `cmux` binaries; never message real agents during verification. See `tests/AGENTS.md` for fixture conventions.
- Open decisions live in `docs/open-questions.md`. Architecture decisions belong in `docs/adr/`; write ADRs only when explicitly requested. See `docs/AGENTS.md` for documentation rules.
- Add hooks or guardrails only for a demonstrated need.
