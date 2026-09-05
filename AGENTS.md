# Director

- The system prompt is `ROLE.md`. App mechanics live in `.agents/skills/director-bb/` and `director-cmux/`. Keep the prompt about the role and the skills about their app.
- Director watches only the app it was launched in. `scan.py` detects it from `BB_THREAD_ID` or `CMUX_SURFACE_ID`; `bb_app.py` reads bb only, `cmux_app.py` reads cmux only. Keep it that way and keep the tests that prove it.
- Keep public code and docs reusable. Personal rules, transcripts, logs, and scans belong in gitignored `private/` (`private/judgment/`, `private/log.jsonl`, `private/scans/`). Local migration context is in `private/brief.md`.
- Preserve manual approval for agent messages and disabled random checks (Q27). `memory.py` keeps the dormant sampler with weights and seed so old scans replay.
- Preserve historical logs and scans. Old records without a state snapshot stay eligible until reviewed with a new scan. Cooldown starts at the last recorded review; scanning alone never resets it.
- Test: `python3 -m unittest discover -s . -p 'test_*.py'`. CLI tests use fake `bb` and `cmux` binaries; never message real agents during verification.
- Open decisions live in `open-questions.md`. Architecture decisions belong in `docs/adr/`; write ADRs only when explicitly requested.
- Add hooks or guardrails only for a demonstrated need.
