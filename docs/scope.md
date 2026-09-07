# Scope

- Preserve the operator's working preferences through real use. Shared instructions own the review mechanics; personal judgment, teaching, and policy belong in `profile/`. See [profile.md](profile.md).
- Director is an agent that can be launched inside bb or cmux.
- It watches only the interface where it was launched: bb watches bb; cmux watches cmux.
- In cmux, an exited agent remains eligible for review while its terminal is still open. Director judges whether work remains; the existing approval requirement still applies before resuming it. (05-09-2026, option B; implemented.)
- cmux review history follows the agent session. A new conversation in the same terminal starts its own history; the same session keeps its history if it moves terminals. (05-09-2026, option B; implemented.)
- Use a few scripts and Markdown files.
- Personal project and thread weights adjust ranking within existing urgency groups. See [priorities.md](priorities.md) for configuration and scan evidence.
- Use one system prompt describing the system and Director’s role, without app-specific commands or mechanics.
- The prompt points to separate bundled skills for bb and cmux; the agent reads the skill for its launch interface.
- Build those skills as shorter, simpler versions of the existing `cmux`, `nagent`, and `bb-cli` skills, containing only what Director needs.
- Add hooks and guardrails only when a real need appears.
- Keep the system as simple and minimal as possible.
- Installer work includes setup, launch, update, uninstall, and release assets. Each source checkout or installation keeps its own profile and state. Updates preserve personal data; fresh setup creates missing examples only. See `install.md`.
