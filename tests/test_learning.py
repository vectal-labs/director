import json
from pathlib import Path
import shutil
import unittest

from tests.test_cli import CliTests


class LearningTests(unittest.TestCase):
    save_fixture = CliTests.save_fixture
    called = CliTests.called
    cli = CliTests.cli
    scan = CliTests.scan

    def setUp(self):
        CliTests.setUp(self)
        for name in ('learning.py',):
            source = Path(__file__).resolve().parents[1] / 'director' / name
            if source.exists():
                shutil.copy(source, self.root / 'director' / name)
        self.fixture['bb']['threads'][0]['status'] = 'active'
        self.fixture['bb']['events']['a'] = [
            self.event(1, 'agentMessage', 'Should I add a queue?'),
            self.prompt(2, 'Keep it simple for now.'),
            self.prompt(3, 'Continue automatically.', initiator='agent'),
            self.event(4, 'agentMessage', 'I will keep the existing worker.'),
        ]
        self.save_fixture()

    def event(self, seq, kind, text):
        return {'id': str(seq), 'seq': seq, 'createdAt': 1000 + seq,
                'type': 'item/completed', 'data': {'item': {'type': kind, 'text': text}}}

    def prompt(self, seq, text, initiator='user'):
        return {'id': str(seq), 'seq': seq, 'createdAt': 1000 + seq,
                'type': 'client/turn/requested',
                'data': {'initiator': initiator, 'systemMessageKind': 'unlabeled', 'input': [{'type': 'text', 'text': text}]}}

    def observe(self, *args):
        return json.loads(self.cli('learning.py', 'observe', *args).stdout)

    def test_learns_from_human_input_in_running_threads_without_intervening(self):
        self.assertEqual(self.scan()['candidates'], [])
        pack = self.observe()
        session = pack['sessions'][0]
        self.assertEqual(session['status'], 'active')
        humans = [m for m in session['messages'] if m['role'] == 'human']
        self.assertEqual([m['text'] for m in humans], ['Keep it simple for now.'])
        self.assertEqual(session['messages'][0]['text'], 'Should I add a queue?')
        self.assertEqual(session['messages'][-1]['text'], 'I will keep the existing worker.')
        self.assertTrue((self.root / pack['snapshot']).exists())
        self.assertNotIn('cmux', self.called())
        self.assertFalse((self.root / 'profile').exists())
        self.assertFalse((self.root / 'state' / 'log.jsonl').exists())

    def test_questions_and_answers_survive_new_invocations_without_fake_reviews(self):
        pack = self.observe()
        source = next(m['source'] for m in pack['sessions'][0]['messages'] if m['role'] == 'human')
        question = json.loads(self.cli('learning.py', 'ask', '--snapshot', pack['snapshot'],
                                      '--source', source, '--question', 'Why keep the worker?').stdout)
        again = self.observe()
        self.assertEqual(again['questions'][0]['id'], question['id'])
        blocked = self.cli('learning.py', 'ask', '--snapshot', pack['snapshot'], '--source', source,
                           '--question', 'Why not add a queue?', check=False)
        self.assertNotEqual(blocked.returncode, 0)
        self.cli('learning.py', 'answer', '--id', question['id'], '--text', 'This project has 1 user.')
        recalled = self.observe()
        self.assertEqual(recalled['questions'][0]['answer'], 'This project has 1 user.')
        self.assertEqual(recalled['questions'][0]['status'], 'answered')
        self.assertFalse((self.root / 'state' / 'log.jsonl').exists())
        self.assertFalse((self.root / 'profile' / 'lessons.jsonl').exists())
        duplicate = self.cli('learning.py', 'ask', '--snapshot', pack['snapshot'], '--source', source,
                             '--question', 'Why keep it simple?', check=False)
        self.assertNotEqual(duplicate.returncode, 0)

    def test_old_session_can_be_loaded_without_recency_or_status_filter(self):
        self.fixture['bb']['threads'][0].update(updatedAt=1, status='idle')
        self.save_fixture()
        pack = self.observe('--session', 'a')
        self.assertEqual(pack['sessions'][0]['id'], 'a')
        self.assertEqual(len(pack['sessions'][0]['messages']), 4)

    def test_human_speaker_is_unknown_when_initiator_is_missing(self):
        self.fixture['bb']['events']['a'][1]['data'].pop('initiator')
        self.save_fixture()
        pack = self.observe()
        message = pack['sessions'][0]['messages'][1]
        self.assertEqual(message['role'], 'unknown')
        bad = self.cli('learning.py', 'ask', '--snapshot', pack['snapshot'], '--source', message['source'],
                       '--question', 'Why?', check=False)
        self.assertNotEqual(bad.returncode, 0)


    def test_archived_context_is_loaded_but_self_hidden_deleted_and_remote_are_not(self):
        base = self.fixture['bb']['threads'][0]
        archived = {**base, 'id': 'old', 'archivedAt': 42, 'updatedAt': 1}
        self.fixture['bb']['archived'] = [archived]
        self.fixture['bb']['events']['old'] = [self.prompt(1, 'Old explicit instruction.')]
        self.fixture['bb']['threads'] += [
            {**base, 'id': 'self'}, {**base, 'id': 'hidden', 'visibility': 'hidden'},
            {**base, 'id': 'deleted', 'deletedAt': 1}, {**base, 'id': 'remote', 'environmentHostId': 'elsewhere'},
        ]
        self.save_fixture()
        pack = self.observe()
        self.assertEqual([s['id'] for s in pack['sessions']], ['a', 'old'])
        self.assertTrue(pack['sessions'][1]['archived'])
        self.assertNotEqual(self.cli('learning.py', 'observe', '--session', 'hidden', check=False).returncode, 0)

    def test_limits_and_source_truncation_are_explicit(self):
        self.fixture['bb']['events']['a'][1]['data']['input'][0]['text'] = 'x' * 7000
        self.fixture['bb']['threads'].append({**self.fixture['bb']['threads'][0], 'id': 'other', 'updatedAt': 1})
        self.save_fixture()
        pack = self.observe('--limit', '1', '--messages', '3')
        self.assertTrue(pack['limited'])
        self.assertTrue(pack['sessions'][0]['truncated'])
        self.assertEqual(pack['sessions'][0]['message_count'], 4)
        self.assertEqual(len(pack['sessions'][0]['messages'][0]['text']), 6000)
        self.assertTrue(pack['sessions'][0]['messages'][0]['truncated'])

    def test_malformed_history_does_not_become_silent_absence_of_activity(self):
        self.fixture['bb']['events']['a'][1]['data']['input'] = None
        self.save_fixture()
        pack = self.observe()
        self.assertEqual(pack['sessions'], [])
        self.assertEqual(pack['coverage'], 'partial')
        self.assertEqual(pack['errors'][0]['id'], 'a')

    def test_sender_thread_overrides_user_label_and_duplicate_events_are_not_new_evidence(self):
        event = self.fixture['bb']['events']['a'][1]
        event['data']['senderThreadId'] = 'another-agent'
        self.fixture['bb']['events']['a'].append(event)
        self.save_fixture()
        messages = self.observe()['sessions'][0]['messages']
        self.assertEqual(len(messages), 4)
        self.assertFalse(any(m['role'] == 'human' for m in messages))

    def test_dismissed_question_remains_dismissed_and_forged_source_is_rejected(self):
        pack = self.observe()
        source = pack['sessions'][0]['messages'][1]['source']
        invalid = self.cli('learning.py', 'ask', '--snapshot', pack['snapshot'], '--source', 'nonexistent',
                           '--question', 'Why?', check=False)
        self.assertNotEqual(invalid.returncode, 0)
        q = json.loads(self.cli('learning.py', 'ask', '--snapshot', pack['snapshot'], '--source', source,
                               '--question', 'Why keep it simple?').stdout)
        self.cli('learning.py', 'dismiss', '--id', q['id'], '--text', 'Skip this question.')
        remembered = self.observe()['questions'][0]
        self.assertEqual(remembered['status'], 'dismissed')
        repeated = self.cli('learning.py', 'ask', '--snapshot', pack['snapshot'], '--source', source,
                            '--question', 'Why keep it simple?', check=False)
        self.assertNotEqual(repeated.returncode, 0)
        self.assertNotEqual(self.cli('learning.py', 'answer', '--id', q['id'], '--text', 'yes', check=False).returncode, 0)

    def cmux_fixture(self):
        self.env.pop('BB_THREAD_ID')
        self.env['CMUX_SURFACE_ID'] = 'SELF'
        self.fixture = {'cmux': {'sessions': [
            {'surface_id': 'OLD', 'session_id': 'past', 'agent': 'claude', 'agent_lifecycle': 'idle',
             'active_for_surface': False, 'updated_at_unix': 1, 'cwd': '/project'},
            {'surface_id': 'OPEN', 'session_id': 'current', 'agent': 'claude', 'agent_lifecycle': 'running',
             'active_for_surface': True, 'updated_at_unix': 2, 'cwd': '/project'},
        ], 'tree': {'windows': [{'workspaces': [{'id': 'WORKSPACE', 'panes': [{'surfaces': [
            {'id': 'OPEN', 'type': 'terminal'}]}]}]}]}, 'screens': {'OPEN': 'A predicted draft, not human speech.'}}}

    def test_cmux_loads_registered_closed_session_and_running_screen_without_bb(self):
        self.cmux_fixture()
        transcript = self.root / 'previous.jsonl'
        transcript.write_text('{"role":"user","content":"Keep the existing service."}\n')
        self.fixture['cmux']['sessions'][0]['transcript_path'] = str(transcript)
        self.save_fixture()
        pack = self.observe()
        self.assertEqual(pack['app'], 'cmux')
        self.assertEqual([s['id'] for s in pack['sessions']], ['cmux:claude:current', 'cmux:claude:past'])
        self.assertTrue(all(m['role'] == 'context_unattributed' for s in pack['sessions'] for m in s['messages']))
        self.assertIn('Keep the existing service.', pack['sessions'][1]['messages'][0]['text'])
        self.assertEqual(set(self.called()), {'cmux'})

    def test_cmux_never_reads_reused_terminal_as_an_old_sessions_conversation(self):
        self.cmux_fixture()
        self.fixture['cmux']['sessions'][0]['surface_id'] = 'OPEN'
        self.save_fixture()
        pack = self.observe()
        self.assertEqual([s['id'] for s in pack['sessions']], ['cmux:claude:current'])
        self.assertEqual(pack['coverage'], 'partial')
        self.assertEqual(pack['errors'][0]['id'], 'cmux:claude:past')

    def test_cmux_recalls_previous_director_on_reused_self_surface(self):
        self.cmux_fixture()
        old = self.fixture['cmux']['sessions'][0]
        old['surface_id'] = 'SELF'
        transcript = self.root / 'old-director.jsonl'
        transcript.write_text('Earlier operator explanation.\n')
        old['transcript_path'] = str(transcript)
        self.fixture['cmux']['sessions'].append({**old, 'session_id': 'this-director',
                                                'active_for_surface': True, 'updated_at_unix': 3})
        self.save_fixture()
        pack = self.observe()
        self.assertIn('cmux:claude:past', [s['id'] for s in pack['sessions']])
        self.assertNotIn('cmux:claude:this-director', [s['id'] for s in pack['sessions']])

    def test_wrong_launch_app_and_missing_environment_do_not_read_any_app(self):
        invalid = self.cli('learning.py', '--app', 'cmux', 'observe', check=False)
        self.assertNotEqual(invalid.returncode, 0)
        self.assertEqual(self.called(), [])
        env = {k: v for k, v in self.env.items() if k != 'BB_THREAD_ID'}
        invalid = self.cli('learning.py', 'observe', env=env, check=False)
        self.assertNotEqual(invalid.returncode, 0)
        self.assertEqual(self.called(), [])

    def test_corrupt_question_history_is_not_discarded(self):
        path = self.root / 'state' / 'learning' / 'questions.jsonl'
        path.parent.mkdir(parents=True)
        path.write_text('{"broken":')
        before = path.read_bytes()
        self.assertNotEqual(self.cli('learning.py', 'observe', check=False).returncode, 0)
        self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
