# Open questions

Current boundary: the shared system loads personal teaching from `profile/` and
operational records from `state/`. `private/` is unrelated storage. This updates
the placement and starter-file discussion below; see `docs/profile.md`.

1. **Public rules — deferred.** The earlier “resolved” conclusion was incorrect. Keep using David’s full rules and history. Public starter-file contents are a later release question, not current implementation work. See `docs/scope.md` and `private/qa.md`.
2. **Naming — deferred unless it affects current use.** The role says “operator”; the persisted log schema still says “David”. Do not do a naming migration only for hypothetical future users.
3. **Exited cmux agents — implemented.** David chose B: review exited agents whose terminals are still open. This replaces the blanket exclusion of dead processes. Existing approval requirements still apply before resuming anything. See `docs/scope.md` and `private/qa.md` (05-09-2026).
4. **cmux review identity — implemented.** David chose B: history follows the agent session. A new conversation in the same terminal gets its own history; moving the same session to another terminal preserves its history. This replaces the current terminal-based review identity. See `docs/scope.md` and `private/qa.md` (05-09-2026).

Automated tests cover both cmux changes. Live cmux validation remains pending because the app is closed.

Accepted extraction scope remains in `docs/scope.md`. Answers will be recorded in `private/qa.md`.
