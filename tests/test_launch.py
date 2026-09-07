"""Lifecycle integration against stateful fake app CLIs; never talk to real agents."""
import contextlib
import importlib.util
import io
import json
import os
import pty
import select
import shlex
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('director_launch', Path(__file__).resolve().parents[1] / 'director' / 'launch.py')
launch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launch)

FAKE = r'''
import json, os, sys
from pathlib import Path
path = Path(os.environ['FAKE_STATE'])
state = json.loads(path.read_text())
app = Path(sys.argv[0]).name
args = [a for a in sys.argv[1:] if a != '--json']
state['calls'].append([app, *args])
def save(): path.write_text(json.dumps(state))
def reply(value):
    save()
    print(json.dumps(value))
    sys.exit(0)
def arg(flag): return args[args.index(flag)+1]
def fail():
    save()
    sys.exit(1)
if state.get('offline') == app or args[:2] == state.get('fail_command'): fail()
if app in ('codex', 'claude'):
    save()
    print('--model --add-dir --sandbox --ask-for-approval --permission-mode')
    sys.exit(0)
if app == 'bb':
    if args[:2] == ['project', 'list']: reply(state['projects'])
    if args[:2] == ['project', 'create']:
        row = {'id':'proj_director', 'name':arg('--name'), 'sources':[
            {'path':arg('--root'), 'hostId':'host_local'}]}
        state['projects'].append(row)
        reply(row)
    if args[:2] == ['project', 'delete']:
        state['projects'] = [p for p in state['projects'] if p['id'] != args[2]]
        reply({'ok':True})
    if args[:2] == ['provider', 'list']:
        reply([{'id':'codex','available':True,'capabilities':{'permissionModes':['accept-edits','auto','full']}},
               {'id':'pi','available':True,'capabilities':{'permissionModes':['full']}}])
    if args[:2] == ['provider', 'models']:
        reply([{'id':'economy','isDefault':False},{'id':'frontier','isDefault':True}])
    if args[:2] == ['thread', 'list']:
        reply([v['thread'] for v in state['threads'].values() if
               v['thread']['projectId'] == arg('--project') and
               bool(v['thread'].get('archivedAt')) == ('--archived' in args)])
    if args[:2] == ['thread', 'spawn']:
        tid = 'thr_' + str(len(state['threads']) + 1)
        row = {'thread':{'id':tid,'projectId':arg('--project'),'title':arg('--title'), 'status':'active'},
               'environment':{'hostId':arg('--machine'),'path':arg('--environment')},
               'history':[{'input':[{'type':'text','text':arg('--prompt')}]}]}
        state['threads'][tid] = row
        if state.get('spawn_lost_reply'):
            save()
            print('connection lost after creation')
            sys.exit(1)
        reply(row)
    if args[:2] == ['thread','history']: reply(state['threads'][args[2]]['history'])
    if args[:2] == ['thread','show']:
        if args[2] not in state['threads']:
            print('Error: HTTP 404: Thread not found', file=sys.stderr)
            fail()
        reply(state['threads'][args[2]])
    if args[:2] == ['thread','stop']:
        if not state.get('stop_stuck'): state['threads'][args[2]]['thread']['status'] = 'idle'
        reply({'ok':True})
    if args[:2] == ['thread','delete']:
        del state['threads'][args[2]]
        reply({'ok':True})
if app == 'cmux':
    if args[0] == 'tree': reply({'windows':[{'workspaces':state['workspaces']}]})
    if args[0] == 'new-workspace':
        wid = 'ws-' + str(len(state['workspaces']) + 1)
        state['workspaces'].append({'id':wid,'title':arg('--name'),'cwd':arg('--cwd'),
            'panes':[{'surfaces':[{'id':'surface-'+wid,'type':'terminal'}]}]})
        reply({'workspace_id':wid})
    if args[0] in ['send','send-key']: reply({'ok':True})
    if args[0] == 'close-workspace':
        if not state.get('stop_stuck'):
            state['workspaces'] = [w for w in state['workspaces'] if w['id'] != arg('--workspace')]
        reply({'ok':True})
fail()
'''


class LaunchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.root = self.home / 'installation' / 'current'
        self.root.mkdir(parents=True)
        self.state_dir = self.root / 'state'
        self.state_dir.mkdir()
        (self.root / 'profile').mkdir()
        self.bin = self.home / 'bin'
        self.bin.mkdir()
        self.statefile = self.home / 'fake-state.json'
        self.write_state({'calls': [], 'projects': [], 'threads': {}, 'workspaces': []})
        for app in ('bb', 'cmux', 'claude', 'codex'):
            executable = self.bin / app
            executable.write_text(f'#!{sys.executable}\n' + FAKE)
            executable.chmod(0o755)
        env = {k:v for k,v in os.environ.items() if not k.startswith(('BB_', 'CMUX_'))}
        env.update(PATH=str(self.bin), FAKE_STATE=str(self.statefile))
        environment = patch.dict(os.environ, env, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.output = io.StringIO()
        capture = contextlib.redirect_stdout(self.output)
        capture.__enter__()
        self.addCleanup(capture.__exit__, None, None, None)

    def state(self):
        return json.loads(self.statefile.read_text())

    def write_state(self, state):
        self.statefile.write_text(json.dumps(state))

    def set_state(self, **changes):
        state = self.state()
        state.update(changes)
        self.write_state(state)

    def configure(self, app='bb', provider=None, model=None):
        config = launch.configure(app, provider, model, self.root, self.state_dir, interactive=False)
        (self.state_dir / 'config.json').write_text(json.dumps(config))
        return config

    def ledger(self):
        return json.loads((self.state_dir / 'launches.json').read_text())

    def calls(self, app, *prefix):
        return [c for c in self.state()['calls'] if c[:len(prefix)+1] == [app, *prefix]]

    def test_bb_configure_discovers_local_host_safe_provider_and_default_model(self):
        config = self.configure()
        self.assertEqual(config['provider'], 'codex')
        self.assertEqual(config['model'], 'frontier')
        self.assertEqual(config['host_id'], 'host_local')
        self.assertIn('--machine', self.calls('bb', 'provider', 'list')[0])
        self.assertTrue(self.ledger()['projects'][0]['owned'])
        with self.assertRaisesRegex(launch.LaunchError, 'Unavailable provider'):
            self.configure(provider='pi')

    def test_rerun_preserves_provider_model_and_project(self):
        config = self.configure(model='economy')
        rerun = launch.configure(None, None, None, self.root, self.state_dir, interactive=False)
        self.assertEqual(config, rerun)
        self.assertEqual(len(self.calls('bb', 'project', 'create')), 1)

    def test_unattended_setup_requires_app_when_both_installed(self):
        with self.assertRaisesRegex(launch.LaunchError, 'Both bb and cmux'):
            launch.configure(None, None, None, self.root, self.state_dir, interactive=False)
        self.assertEqual(self.state()['calls'], [])

    def test_bb_start_and_rerun_create_one_correctly_scoped_thread(self):
        config = self.configure()
        launch.start(config, self.root, self.state_dir)
        launch.start(config, self.root, self.state_dir)
        calls = self.calls('bb', 'thread', 'spawn')
        self.assertEqual(len(calls), 1)
        call = calls[0]
        self.assertEqual(call[call.index('--environment')+1], str(self.root))
        self.assertEqual(call[call.index('--permission-mode')+1], 'accept-edits')
        self.assertEqual(call[call.index('--title')+1], 'DIRECTOR')
        self.assertIn('Read ROLE.md', call[call.index('--prompt')+1])
        self.assertEqual(self.ledger()['sessions'][0]['phase'], 'started')

    def test_bb_spawn_failure_keeps_ownership_and_blocks_duplicate(self):
        config = self.configure()
        self.set_state(spawn_lost_reply=True)
        with self.assertRaises(launch.LaunchError):
            launch.start(config, self.root, self.state_dir)
        self.assertEqual(len(self.ledger()['sessions']), 1)
        with self.assertRaisesRegex(launch.LaunchError, 'previous launch was interrupted'):
            launch.start(config, self.root, self.state_dir)
        self.assertEqual(len(self.calls('bb', 'thread', 'spawn')), 1)
        launch.stop_owned(self.root, self.state_dir, purge=True)
        self.assertEqual(self.state()['threads'], {})
        self.assertEqual(self.state()['projects'], [])

    def test_bb_uninstall_preserves_app_history_and_other_threads(self):
        config = self.configure()
        launch.start(config, self.root, self.state_dir)
        state = self.state()
        state['threads']['other'] = {'thread':{'id':'other','projectId':config['project_id'],
                                             'title':'Human work','status':'active'},
                                    'environment':{'hostId':'host_local','path':str(self.root)}}
        self.write_state(state)
        launch.stop_owned(self.root, self.state_dir)
        self.assertEqual(self.state()['threads']['other']['thread']['status'], 'active')
        self.assertEqual(self.state()['threads']['thr_1']['thread']['status'], 'idle')
        self.assertFalse(self.calls('bb', 'thread', 'delete'))

    def test_bb_purge_removes_only_owned_app_records(self):
        config = self.configure()
        launch.start(config, self.root, self.state_dir)
        launch.stop_owned(self.root, self.state_dir, purge=True)
        self.assertEqual(self.state()['threads'], {})
        self.assertEqual(self.state()['projects'], [])

    def test_bb_reused_project_is_never_deleted(self):
        self.set_state(projects=[{'id':'existing','name':'My project',
                                  'sources':[{'path':str(self.root),'hostId':'host_local'}]}])
        config = self.configure()
        launch.start(config, self.root, self.state_dir)
        launch.stop_owned(self.root, self.state_dir, purge=True)
        self.assertFalse(self.calls('bb', 'project', 'delete'))
        self.assertFalse(self.ledger()['projects'][0]['owned'])

    def test_bb_purge_keeps_project_with_unowned_thread(self):
        config = self.configure()
        launch.start(config, self.root, self.state_dir)
        state = self.state()
        state['threads']['other'] = {'thread':{'id':'other','projectId':config['project_id'],
                                             'title':'Human work','status':'active'}}
        self.write_state(state)
        with self.assertRaisesRegex(launch.LaunchError, 'contains other threads'):
            launch.stop_owned(self.root, self.state_dir, purge=True)
        self.assertTrue(self.state()['projects'])
        self.assertFalse(self.calls('bb', 'project', 'delete'))

    def test_bb_ownership_changes_prevent_stop(self):
        config = self.configure()
        launch.start(config, self.root, self.state_dir)
        state = self.state()
        state['threads']['thr_1']['environment']['path'] = '/someone/else'
        self.write_state(state)
        with self.assertRaisesRegex(launch.LaunchError, 'ownership metadata changed'):
            launch.stop_owned(self.root, self.state_dir)
        self.assertFalse(self.calls('bb', 'thread', 'stop'))

    def test_bb_thread_moved_to_another_project_blocks_stop(self):
        config = self.configure()
        launch.start(config, self.root, self.state_dir)
        state = self.state()
        state['threads']['thr_1']['thread']['projectId'] = 'another-project'
        self.write_state(state)
        with self.assertRaisesRegex(launch.LaunchError, 'ownership metadata changed'):
            launch.stop_owned(self.root, self.state_dir)
        self.assertFalse(self.calls('bb', 'thread', 'stop'))

    def test_offline_app_and_unfinished_stop_fail_closed(self):
        config = self.configure()
        launch.start(config, self.root, self.state_dir)
        self.set_state(offline='bb')
        with self.assertRaises(launch.LaunchError):
            launch.stop_owned(self.root, self.state_dir)
        self.set_state(offline=None, stop_stuck=True)
        with self.assertRaisesRegex(launch.LaunchError, 'still stopping'):
            launch.stop_owned(self.root, self.state_dir)
        self.assertNotIn('stopped', self.ledger()['sessions'][0])

    def test_bb_stop_after_update_uses_recorded_release_path(self):
        config = self.configure()
        launch.start(config, self.root, self.state_dir)
        ledger = self.ledger()
        ledger['sessions'][0]['resolved_root'] = '/install/releases/v1'
        (self.state_dir / 'launches.json').write_text(json.dumps(ledger))
        state = self.state()
        state['threads']['thr_1']['environment']['path'] = '/install/releases/v1'
        self.write_state(state)
        launch.stop_owned(self.root, self.state_dir)
        self.assertEqual(self.state()['threads']['thr_1']['thread']['status'], 'idle')

    def test_cmux_setup_guides_shared_hooks_without_mutations(self):
        config = self.configure('cmux', 'claude')
        self.assertEqual(config['provider'], 'claude')
        self.assertIsNone(config['model'])
        self.assertIn('cmux hooks setup', self.output.getvalue())
        self.assertFalse(self.calls('cmux', 'hooks'))
        self.assertFalse(self.calls('cmux', 'new-workspace'))

    def test_cmux_start_targets_owned_surface_and_reuses_workspace(self):
        config = self.configure('cmux', 'codex', 'custom-model')
        launch.start(config, self.root, self.state_dir)
        launch.start(config, self.root, self.state_dir)
        self.assertEqual(len(self.calls('cmux', 'new-workspace')), 1)
        created = self.calls('cmux', 'new-workspace')[0]
        self.assertEqual(created[created.index('--focus')+1], 'false')
        sent = self.calls('cmux', 'send')[0]
        self.assertIn('surface-ws-1', sent)
        self.assertIn('--sandbox workspace-write', sent[-1])
        self.assertIn('--ask-for-approval on-request', sent[-1])
        self.assertIn('--add-dir ' + shlex.quote(str(self.root / 'profile')), sent[-1])
        self.assertIn('--add-dir ' + shlex.quote(str(self.state_dir)), sent[-1])
        self.assertIn('profile/ teaching and settings', sent[-1])
        self.assertIn('unset BB_THREAD_ID', sent[-1])
        self.assertIn('custom-model', sent[-1])
        self.assertNotIn('bypass', sent[-1])
        self.assertFalse(self.calls('bb'))

    def test_cmux_uninstall_does_not_close_other_workspace(self):
        config = self.configure('cmux', 'claude')
        launch.start(config, self.root, self.state_dir)
        state = self.state()
        state['workspaces'].append({'id':'human-workspace','title':'Human work','panes':[]})
        self.write_state(state)
        launch.stop_owned(self.root, self.state_dir, purge=True)
        self.assertEqual([w['id'] for w in self.state()['workspaces']], ['human-workspace'])
        self.assertFalse(self.calls('cmux', 'hooks'))

    def test_cmux_added_surface_blocks_workspace_close(self):
        config = self.configure('cmux', 'claude')
        launch.start(config, self.root, self.state_dir)
        state = self.state()
        state['workspaces'][0]['panes'][0]['surfaces'].append({'id':'human-terminal','type':'terminal'})
        self.write_state(state)
        with self.assertRaisesRegex(launch.LaunchError, 'layout or title changed'):
            launch.stop_owned(self.root, self.state_dir)
        self.assertFalse(self.calls('cmux', 'close-workspace'))

    def test_cmux_failed_send_is_tracked_and_never_retried_into_uncertain_shell(self):
        config = self.configure('cmux', 'claude')
        self.set_state(fail_command=['send-key','--workspace'])
        with self.assertRaises(launch.LaunchError):
            launch.start(config, self.root, self.state_dir)
        with self.assertRaisesRegex(launch.LaunchError, 'previous launch was interrupted'):
            launch.start(config, self.root, self.state_dir)
        self.assertEqual(len(self.calls('cmux', 'send')), 1)
        self.set_state(fail_command=None)
        launch.stop_owned(self.root, self.state_dir)
        self.assertEqual(self.state()['workspaces'], [])

    def test_terminal_question_reads_tty_when_stdin_is_a_pipeline(self):
        child, master = pty.fork()
        if child == 0:
            try:
                sys.stdin = io.StringIO('do not consume pipeline stdin\n')
                answer = launch._ask('Choose app', 'bb')
                os.write(1, ('CHOSEN=' + answer + '\n').encode())
                os._exit(0)
            except BaseException:
                os._exit(1)
        try:
            ready, _, _ = select.select([master], [], [], 3)
            self.assertTrue(ready, 'Question did not reach the controlling terminal')
            prompt = os.read(master, 4096)
            self.assertIn(b'Choose app', prompt)
            os.write(master, b'cmux\n')
            output = b''
            for _ in range(4):
                ready, _, _ = select.select([master], [], [], 3)
                if not ready:
                    break
                try:
                    output += os.read(master, 4096)
                except OSError:
                    break
                if b'CHOSEN=cmux' in output:
                    break
            self.assertIn(b'CHOSEN=cmux', output)
            _, status = os.waitpid(child, 0)
            child = None
            self.assertEqual(status, 0)
        finally:
            os.close(master)
            if child:
                os.kill(child, 9)
                os.waitpid(child, 0)

    def test_corrupt_ownership_file_blocks_actions(self):
        (self.state_dir / 'launches.json').write_text('broken json')
        with self.assertRaisesRegex(launch.LaunchError, 'Cannot read'):
            launch.stop_owned(self.root, self.state_dir)
        self.assertFalse(self.state()['calls'])


if __name__ == '__main__':
    unittest.main()
