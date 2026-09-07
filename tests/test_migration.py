"""Migration preserves personal evidence and refuses ambiguous data ownership."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from director import migrate


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write(self, name, contents):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
        return path

    def legacy(self):
        contents = {
            'judgment/what.md': 'My exact scope.\n',
            'judgment/how.md': 'My exact style.\n',
            'judgment/limits.md': 'Ask before acting.\n',
            'judgment/qa.md': 'Q01: My exact words.\n',
            'log.jsonl': '{"run":1,"decision":"leave","picked":"one","ts":"2026-09-01T00:00:00+00:00"}\n',
            'scans/old.json': '{"original":"evidence"}\n',
            'priorities.json': '{"projects":{"example":2}}\n',
            'config.json': '{"app":"bb"}\n',
            'launches.json': '{"schema":1,"sessions":[]}\n',
        }
        for name, text in contents.items():
            self.write('private/' + name, text)
        return contents

    def test_cli_migrates_known_files_preserves_notes_and_can_run_again(self):
        contents = self.legacy()
        note = self.write('private/unrelated/notes.md', 'Leave this note exactly here.\n')
        command = [sys.executable, str(Path(migrate.__file__)), '--root', str(self.root)]
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        self.assertTrue(json.loads(result.stdout)['migrated'])
        for old, new in migrate.FILES.items():
            self.assertEqual((self.root / new).read_text(), contents[old])
            self.assertTrue((self.root / 'private' / old).is_symlink())
            self.assertEqual((self.root / 'state/migration-backup/private' / old).read_text(), contents[old])
        self.assertEqual((self.root / 'state/scans/old.json').read_text(), contents['scans/old.json'])
        self.assertTrue((self.root / 'private/scans').is_symlink())
        self.assertEqual(note.read_text(), 'Leave this note exactly here.\n')
        repeated = subprocess.run(command, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(repeated.stdout)['migrated'], [])

    def test_conflict_preflight_changes_no_teaching_or_legacy_data(self):
        self.legacy()
        destination = self.write('state/launches.json', 'different active installation\n')
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            migrate.migrate(self.root)
        self.assertFalse((self.root / 'profile').exists())
        self.assertFalse((self.root / 'private/log.jsonl').is_symlink())
        self.assertEqual(destination.read_text(), 'different active installation\n')

    def test_matching_destinations_are_not_overwritten(self):
        self.legacy()
        target = self.write('profile/what.md', 'My exact scope.\n')
        before = target.stat().st_ino
        migrate.migrate(self.root)
        self.assertEqual(target.stat().st_ino, before)
        self.assertTrue((self.root / 'private/judgment/what.md').is_symlink())

    def test_existing_log_handle_keeps_writing_to_canonical_log(self):
        self.legacy()
        legacy = self.root / 'private/log.jsonl'
        with legacy.open('a') as writer:
            inode = os.fstat(writer.fileno()).st_ino
            migrate.migrate(self.root)
            writer.write('new event\n')
            writer.flush()
        self.assertEqual((self.root / 'state/log.jsonl').stat().st_ino, inode)
        self.assertTrue((self.root / 'state/log.jsonl').read_text().endswith('new event\n'))
        self.assertFalse((self.root / 'state/migration-backup/private/log.jsonl').read_text().endswith('new event\n'))

    def test_matching_log_copy_is_refused_to_preserve_open_writers(self):
        contents = self.legacy()
        self.write('state/log.jsonl', contents['log.jsonl'])
        with (self.root / 'private/log.jsonl').open('a') as writer:
            with self.assertRaisesRegex(ValueError, 'separate writer handles'):
                migrate.migrate(self.root)
            writer.write('still reachable\n')
        self.assertTrue((self.root / 'private/log.jsonl').read_text().endswith('still reachable\n'))
        self.assertFalse((self.root / 'profile').exists())

    def test_malformed_history_refuses_before_moving_any_teaching(self):
        self.legacy()
        log = self.write('private/log.jsonl', 'unreadable history\n')
        with self.assertRaisesRegex(ValueError, 'invalid record'):
            migrate.migrate(self.root)
        self.assertFalse(log.is_symlink())
        self.assertEqual(log.read_text(), 'unreadable history\n')
        self.assertFalse((self.root / 'profile').exists())

    def test_migration_makes_durable_teaching_available_without_original_log(self):
        self.legacy()
        event = {'kind': 'override', 'run': 1, 'ts': '2026-09-01T00:01:00+00:00',
                 'david': 'Keep explanations brief.', 'decision': 'leave', 'rule': 'Q01',
                 'lesson': {'id': '1.1', 'kind': 'general_preference', 'scope': 'general',
                            'app': None, 'target': None, 'interpretation': 'Use short explanations.',
                            'reason': 'Explicit preference.', 'applies_when': 'Explaining a review.'}}
        log = self.root / 'private/log.jsonl'
        with log.open('a') as stream:
            stream.write(json.dumps(event) + '\n')
        migrate.migrate(self.root)
        journal = self.root / 'profile/lessons.jsonl'
        self.assertEqual(json.loads(journal.read_text()), event)
        before = journal.read_bytes()
        migrate.migrate(self.root)
        self.assertEqual(journal.read_bytes(), before)

    def test_legacy_code_settings_are_extracted_without_execution(self):
        self.legacy()
        self.write('director/memory.py', '''raise RuntimeError("Must never execute old code")
RECHECK_SECONDS = 7200
SPOT_CHECK_CHANCE = 0.0
def effective_decision(row):
    return {"example-rule": "leave"}.get(row)
''')
        migrate.migrate(self.root)
        settings = json.loads((self.root / 'profile/settings.json').read_text())
        self.assertEqual(settings['recheck_seconds'], 7200)
        self.assertEqual(settings['legacy_override_decisions'], {'example-rule': 'leave'})

    def test_settings_already_in_profile_win_over_old_defaults(self):
        self.legacy()
        self.write('director/memory.py', 'RECHECK_SECONDS = 7200\n')
        settings = self.write('profile/settings.json', '{"recheck_seconds":120}\n')
        migrate.migrate(self.root)
        self.assertEqual(settings.read_text(), '{"recheck_seconds":120}\n')

    def test_legacy_settings_survive_an_updater_that_already_switched_code(self):
        self.write('director/memory.py', 'from preferences import DEFAULTS\n')
        self.write('releases/v1.0.0/director/memory.py', '''RECHECK_SECONDS = 900
def effective_decision(row):
    return {"prior-rule": "leave"}.get(row)
''')
        self.write('releases/v2.0.0/director/memory.py', 'from preferences import DEFAULTS\n')
        self.write('install.json', '{"versions":["v1.0.0","v2.0.0"]}\n')
        settings = migrate.legacy_settings(self.root)
        self.assertEqual(settings['recheck_seconds'], 900)
        self.assertEqual(settings['legacy_override_decisions'], {'prior-rule': 'leave'})

    def test_pulled_source_checkout_uses_local_previous_code_without_fetching(self):
        self.write('.git', 'gitdir: fake\n')
        self.write('director/memory.py', 'from preferences import DEFAULTS\n')
        responses = [subprocess.CompletedProcess([], 0, 'from preferences import DEFAULTS\n', ''),
                     subprocess.CompletedProcess([], 0, 'RECHECK_SECONDS = 900\n', '')]
        with patch.object(migrate.subprocess, 'run', side_effect=responses) as run:
            self.assertEqual(migrate.legacy_settings(self.root), {'recheck_seconds': 900})
        self.assertEqual([call.args[0][-1] for call in run.call_args_list],
                         ['HEAD:director/memory.py', 'HEAD^:director/memory.py'])

    def test_unrelated_symlinks_are_refused_without_following(self):
        outside = self.write('elsewhere/secret.md', 'do not move\n')
        legacy = self.root / 'private/judgment/what.md'
        legacy.parent.mkdir(parents=True)
        legacy.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'Unexpected data link'):
            migrate.migrate(self.root)
        self.assertEqual(outside.read_text(), 'do not move\n')

    def test_scans_symlink_escape_is_refused_before_any_move(self):
        self.legacy()
        (self.root / 'private/scans/escape').symlink_to(self.root / 'elsewhere')
        with self.assertRaisesRegex(ValueError, 'Unsupported legacy'):
            migrate.migrate(self.root)
        self.assertFalse((self.root / 'private/judgment/what.md').is_symlink())

    def test_interrupted_directory_move_recovers_legacy_alias(self):
        self.legacy()
        original = migrate._link
        def fail_scans(source, target):
            if source.name == 'scans':
                raise OSError('simulated interruption after directory move')
            return original(source, target)
        with patch.object(migrate, '_link', side_effect=fail_scans):
            with self.assertRaisesRegex(OSError, 'simulated'):
                migrate.migrate(self.root)
        self.assertTrue((self.root / 'state/scans/old.json').exists())
        migrate.migrate(self.root)
        self.assertTrue((self.root / 'private/scans').is_symlink())
        self.assertTrue((self.root / 'private/scans/old.json').exists())

    def test_managed_release_resolves_to_installation_root(self):
        self.legacy()
        release = self.root / 'releases/v1.0.0'
        release.mkdir(parents=True)
        for name in ('private', 'profile', 'state'):
            (release / name).symlink_to('../../' + name)
        migrate.migrate(release)
        self.assertTrue((self.root / 'profile/what.md').exists())
        self.assertEqual((release / 'state/log.jsonl').resolve(), (self.root / 'state/log.jsonl').resolve())

    def test_cleanup_keeps_unrelated_private_files(self):
        self.legacy()
        note = self.write('private/notes.md', 'unrelated\n')
        migrate.migrate(self.root)
        migrate.cleanup_legacy_aliases(self.root)
        self.assertEqual(note.read_text(), 'unrelated\n')
        self.assertFalse((self.root / 'private/judgment').exists())
        self.assertTrue((self.root / 'profile/qa.md').exists())


if __name__ == '__main__':
    unittest.main()
