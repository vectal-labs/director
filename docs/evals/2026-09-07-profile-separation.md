# Profile separation verification

Baseline: `2a7fe05`. Shared instructions and runtime now load personal policy from
`profile/`, with operational history in `state/`. No real agents were messaged or
controlled during validation.

## Existing data

An isolated copy of the existing personal data retained all 56 reviews and their
corrections. The old and new history readers produced identical records and
cooldown histories. Ranking was replayed over 62 saved scans with the same input
history, time, seed, and priorities; selections and candidate rankings matched
in every case. Three older scans lacked the fields needed for this replay and
were preserved without claiming a replay result.

The original teaching remains an exact prefix of the migrated Markdown files.
Previously shared personality, judgment, presentation, and manual-stage guidance
were copied verbatim into the personal profile with their original source labeled.

## Behavioral review

An independent agent compared old instructions with the new role and migrated
profile using three synthetic cases: a completed educational response, an unknown
cause requiring investigation, and delivery of an approved message. The expected
leave/investigate/continue decisions, scope boundaries, message style, and manual
approval flow remained equivalent. These are instruction evaluations, not live
agent runs or proof of identical model outputs in every future situation.

Additional instruction checks covered recent human input after approval,
unavailable cmux input history, and queued delivery followed by an idle observation.
All produced skip or unconfirmed outcomes without extra sends, retries, or polling.

## Automated checks

The full fake-app suite passes. It covers profile-only lessons, scope and expiry,
ending copied lessons, ID conflicts, interrupted journal writes, migration backups,
conflicting destinations, legacy open log handles, setup preservation, and release
exclusion of personal files.

Independent review found two migration issues and verified their fixes: an actual
old installer needed new data links when activating the new release, and separate
byte-identical log copies could orphan an open writer. Regression tests exercise
the historical installer itself and refusal of conflicting log inodes.

Shell syntax, release building, and whitespace checks pass. Terminal tests require
host PTY access; all app commands still use isolated fixtures.
