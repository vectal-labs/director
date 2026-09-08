"""Exercise command plugins through the actual CLI with isolated local programs."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

REPO = Path(__file__).resolve().parents[1]


class PluginTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'Director home'
        (self.root / 'director').mkdir(parents=True)
        (self.root / 'profile').mkdir()
        for name in ('plugins.py', 'storage.py'):
            shutil.copy(REPO / 'director' / name, self.root / 'director' / name)
        self.folder = Path(self.tmp.name) / 'private plugin'
        self.folder.mkdir()
        (self.folder / 'SKILL.md').write_text('Private instructions')
        self.manifest = {'version': 1, 'name': 'example', 'command': ['python3', 'run.py'], 'timeout_seconds': 2}
        self.configure()
        self.program("import json,sys\nr=json.load(sys.stdin)\nprint(json.dumps({'version':1,'result':{'operation':r['operation'],'input':r['input']}}))")

    def configure(self):
        (self.root / 'profile/plugins.json').write_text(json.dumps({'example': str(self.folder)}))
        (self.folder / 'plugin.json').write_text(json.dumps(self.manifest))

    def program(self, source):
        (self.folder / 'run.py').write_text(source)

    def cli(self, *args):
        return subprocess.run([sys.executable, str(self.root / 'director/plugins.py'), *args],
                              capture_output=True, text=True, cwd='/', timeout=10)

    def records(self):
        return [json.loads(p.read_text()) for p in (self.root / 'state/plugins/example').glob('*.json')]

    def input(self, value):
        path = self.root / 'change.json'
        path.write_text(json.dumps(value))
        return str(path)

    def test_default_disabled_and_disabling_execute_nothing(self):
        (self.root / 'profile/plugins.json').unlink()
        self.assertEqual(json.loads(self.cli('list').stdout), [])
        self.assertNotEqual(self.cli('observe', 'example').returncode, 0)
        self.assertFalse((self.root / 'state').exists())
        self.configure()
        (self.root / 'profile/plugins.json').write_text('{}')
        self.assertNotEqual(self.cli('observe', 'example').returncode, 0)

    def test_listing_reads_metadata_without_running_plugin(self):
        self.program("raise RuntimeError('must not execute')")
        result = self.cli('list')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [{'name': 'example', 'skill': str((self.folder / 'SKILL.md').resolve())}])
        self.assertFalse((self.root / 'state').exists())

    def test_observe_round_trip_and_persisted_result(self):
        data = {'content': 'Unabridged 日本語 task $HOME `echo unsafe`'}
        result = self.cli('observe', 'example', '--input', self.input(data))
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(result.stdout)
        self.assertEqual(record['result'], {'operation': 'observe', 'input': data})
        self.assertEqual(self.records()[0]['status'], 'succeeded')
        self.assertEqual(os.stat(record['record']).st_mode & 0o777, 0o600)
        self.assertFalse(record['approved'])

    def test_apply_requires_exact_input_and_approval(self):
        path = self.input({'change': 'P2'})
        for args in [('apply', 'example'), ('apply', 'example', '--approved'),
                     ('apply', 'example', '--input', path)]:
            with self.subTest(args=args):
                self.assertNotEqual(self.cli(*args).returncode, 0)
                self.assertEqual(self.records(), [])
        result = self.cli('apply', 'example', '--input', path, '--approved')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['approved'])

    def test_no_shell_expansion_of_arguments(self):
        self.manifest['command'] += ['$(touch SHOULD_NOT_EXIST)']
        self.configure()
        self.program("import json,sys\nprint(json.dumps({'version':1,'result':{'arg':sys.argv[1]}}))")
        result = self.cli('observe', 'example')
        self.assertEqual(json.loads(result.stdout)['result']['arg'], '$(touch SHOULD_NOT_EXIST)')
        self.assertFalse((self.folder / 'SHOULD_NOT_EXIST').exists())

    def test_invalid_manifest_and_missing_skill_do_not_execute(self):
        for change in ({'version': True}, {'command': 'echo hello'}, {'timeout_seconds': 0},
                       {'timeout_seconds': 10**400}, {'name': 'other'}, {'unknown': 1}):
            with self.subTest(change=change):
                (self.folder / 'plugin.json').write_text(json.dumps({**self.manifest, **change}))
                self.assertNotEqual(self.cli('observe', 'example').returncode, 0)
                self.assertEqual(self.records(), [])
        self.configure()
        (self.folder / 'SKILL.md').unlink()
        self.assertNotEqual(self.cli('observe', 'example').returncode, 0)

    def test_invalid_registry_and_input(self):
        for entry in ({'../escape': str(self.folder)}, {'example': './relative'}, []):
            (self.root / 'profile/plugins.json').write_text(json.dumps(entry))
            self.assertNotEqual(self.cli('list').returncode, 0)
        self.configure()
        for data in ([], None, 'text'):
            self.assertNotEqual(self.cli('observe', 'example', '--input', self.input(data)).returncode, 0)
        self.assertEqual(self.records(), [])

    def test_crash_hides_raw_output_and_records_uncertain_apply(self):
        self.program("import sys\nprint('SECRET-MARKER')\nprint('SECRET-MARKER',file=sys.stderr)\nsys.exit(4)")
        result = self.cli('apply', 'example', '--input', self.input({}), '--approved')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('SECRET-MARKER', result.stdout + result.stderr + json.dumps(self.records()))
        self.assertEqual(self.records()[0]['status'], 'uncertain')

    def test_malformed_and_wrong_version_outputs_fail(self):
        for output in ('not JSON', '{"version":1,"result":[]}', '{"version":true,"result":{}}',
                       '{"version":2,"result":{}}', '{"version":1,"result":{"value":NaN}}', '{"version":1,"result":{"value":1e999}}'):
            with self.subTest(output=output):
                self.program('print(' + repr(output) + ')')
                self.assertNotEqual(self.cli('observe', 'example').returncode, 0)
        self.assertTrue(all(r['status'] == 'failed' for r in self.records()))

    def test_timeout_stops_descendants(self):
        marker = self.folder / 'survived'
        self.manifest['timeout_seconds'] = 0.2
        self.configure()
        child = f"import time,pathlib;time.sleep(0.7);pathlib.Path({str(marker)!r}).touch()"
        self.program(f'import subprocess,sys,time\nsubprocess.Popen([sys.executable,"-c",{child!r}])\ntime.sleep(10)')
        result = self.cli('observe', 'example')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('timed out', result.stderr)
        time.sleep(0.8)
        self.assertFalse(marker.exists())

    def test_sigterm_cleans_up_and_records_uncertain_apply(self):
        marker = self.folder / 'survived'
        child = f"import time,pathlib;time.sleep(0.7);pathlib.Path({str(marker)!r}).touch()"
        self.program(f"import pathlib,subprocess,sys,time\nsubprocess.Popen([sys.executable,'-c',{child!r}])\npathlib.Path('ready').touch()\ntime.sleep(10)")
        process = subprocess.Popen([sys.executable, str(self.root / 'director/plugins.py'),
                                    'apply', 'example', '--input', self.input({}), '--approved'],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 3
            while not (self.folder / 'ready').exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue((self.folder / 'ready').exists())
            process.terminate()
            process.communicate(timeout=3)
            self.assertEqual(process.returncode, 130)
            time.sleep(0.8)
            self.assertFalse(marker.exists())
            self.assertEqual(self.records()[0]['status'], 'uncertain')
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate()

    def test_output_limit_and_missing_executable(self):
        self.program("print('x'*1100000)")
        self.assertIn('exceeds 1 MiB', self.cli('observe', 'example').stderr)
        self.manifest['command'] = ['/no/such/command']
        self.configure()
        self.assertNotEqual(self.cli('observe', 'example').returncode, 0)
        self.assertTrue(all(r['status'] == 'failed' for r in self.records()))

    def test_one_plugin_invocation_at_a_time(self):
        self.program("import pathlib,time,json\npathlib.Path('ready').touch()\ntime.sleep(1)\nprint(json.dumps({'version':1,'result':{}}))")
        first = subprocess.Popen([sys.executable, str(self.root / 'director/plugins.py'), 'observe', 'example'],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 3
            while not (self.folder / 'ready').exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue((self.folder / 'ready').exists())
            second = self.cli('observe', 'example')
            self.assertIn('already running', second.stderr)
            first.communicate(timeout=5)
            self.assertEqual(first.returncode, 0)
            self.assertEqual(len(self.records()), 1)
        finally:
            if first.poll() is None:
                first.kill()
            first.communicate()
