# Scope

- Director is an agent that can be launched inside bb or cmux.
- It watches only the interface where it was launched: bb watches bb; cmux watches cmux.
- Use a few scripts and Markdown files.
- Use one system prompt describing the system and Director’s role, without app-specific commands or mechanics.
- The prompt points to separate bundled skills for bb and cmux; the agent reads the skill for its launch interface.
- Build those skills as shorter, simpler versions of the existing `cmux`, `nagent`, and `bb-cli` skills, containing only what Director needs.
- Add hooks and guardrails only when a real need appears.
- Keep the system as simple and minimal as possible.
- Public rule files should be nearly empty starter files, without the operator’s personal preferences. Actual personal rules and history stay in gitignored `private/`.
