"""Viewer contracts: real HTTP, isolated profiles, no app commands or writes."""
import copy
import datetime as dt
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import select
from urllib.parse import urlsplit
from unittest.mock import patch

from director.viewer import reader, server, inline


ROOT = Path(__file__).resolve().parents[1]
QA = '# Teaching\n\n## 1. Diagnose first\n\nDavid: "Find the cause."\n\nSource: original thread, 05-09-2026.\n'
EVENT = {'kind': 'override', 'run': 1, 'ts': '2026-09-05T12:00:00+00:00',
         'decision': 'leave', 'david': 'Wait for the report.', 'rule': 'Q1',
         'lesson': {'id': '1.1', 'kind': 'temporary_instruction', 'scope': 'thread',
                    'app': 'bb', 'target': 'test-thread', 'interpretation': 'Wait for diagnosis.',
                    'reason': 'Cause unknown', 'applies_when': 'Investigation open',
                    'ends_when': 'Operator releases the hold'}}


class ViewerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write(self, path, text):
        dest = self.root / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
        return dest

    def snapshot(self):
        return {str(p.relative_to(self.root)): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in self.root.rglob('*') if p.is_file()}

    def test_empty_profile_does_not_create_directories(self):
        self.assertEqual(reader.read(self.root), {'records': [], 'warnings': []})
        self.assertEqual(list(self.root.iterdir()), [])

    def test_inline_snapshot_is_self_contained_and_escapes_teaching(self):
        self.write('profile/qa.md', QA + '\n## 2. Markup\n</script><script>window.injected=true</script>\n')
        before = self.snapshot()
        html = inline.render(self.root).decode()
        self.assertNotIn('src="/app.js"', html)
        self.assertNotIn('href="/styles.css"', html)
        self.assertIn('id="teaching-snapshot"', html)
        self.assertIn('\\u003c/script>', html)
        self.assertIn("default-src 'none'", html)
        self.assertEqual(self.snapshot(), before)

    def test_inline_export_refuses_overwrite_and_non_private_paths(self):
        self.write('profile/qa.md', QA)
        def export(output):
            return subprocess.run([sys.executable, '-B', '-m', 'director.viewer.inline',
                                   '--root', str(self.root), '--output', output], cwd=self.root,
                                  env={**os.environ, 'PYTHONPATH': str(ROOT)}, capture_output=True, text=True)
        result = export('private/view.html')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('::inline-vis{file="private/view.html" height="960"}', result.stdout)
        self.assertEqual((self.root / 'private/view.html').stat().st_mode & 0o777, 0o600)
        self.assertNotEqual(export('private/view.html').returncode, 0)
        self.assertNotEqual(export('profile/qa.md').returncode, 0)
        self.assertNotEqual(export('public.html').returncode, 0)

    def test_exact_source_summary_and_unknown_storage_date(self):
        self.write('profile/qa.md', QA)
        self.write('profile/how.md', '# Workflow\n\n## Diagnose (Q1)\n\nDo not fix before diagnosis.\n')
        before = self.snapshot()
        with patch('subprocess.run', side_effect=AssertionError('app command')):
            result = reader.read(self.root)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(result['records']), 1)
        item = result['records'][0]
        self.assertEqual(item['evidence'][0]['text'], '"Find the cause."')
        self.assertEqual(item['source_date'], '2026-09-05')
        self.assertIsNone(item['stored_at'])
        self.assertEqual(item['interpretations'][0]['text'], 'Do not fix before diagnosis.')
        self.assertEqual(item['locations'][0]['line'], 3)

    def test_unmatched_sections_preambles_fences_and_duplicate_ids(self):
        self.write('profile/qa.md', QA + '\n## 1. Duplicate\nOther words.\n')
        self.write('profile/how.md', 'Preamble\n\n## Loose\n```md\n## Fake heading\n```\nKeep this.\n\n## Linked (Q1)\nAmbiguous.\n')
        result = reader.read(self.root)
        titles = [r['title'] for r in result['records']]
        self.assertEqual(len(titles), 5)
        self.assertIn('Notes', titles)
        self.assertNotIn('Fake heading', titles)
        self.assertTrue(result['warnings'])
        self.assertEqual(len({r['id'] for r in result['records']}), 5)

    def test_q1_is_not_q10_and_unresolved_references_are_kept(self):
        self.write('profile/qa.md', QA)
        self.write('profile/how.md', '## Other (Q10)\nDo another thing.\n')
        result = reader.read(self.root)['records']
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['interpretations'], [])

    def test_journal_links_keep_scope_and_ending_evidence(self):
        self.write('profile/qa.md', QA)
        ended = {'kind': 'lesson_end', 'run': 1, 'ts': '2026-09-06T12:00:00+00:00',
                 'lesson_id': '1.1', 'evidence': 'Operator released it.'}
        self.write('state/lessons.jsonl', '\n'.join(map(json.dumps, [EVENT, ended])))
        before = self.snapshot()
        result = reader.read(self.root)['records']
        self.assertEqual(result[1]['status'], 'Ended')
        self.assertEqual(result[1]['scope'], 'thread')
        self.assertEqual(result[1]['conditions']['ended'], ended['evidence'])
        self.assertEqual(result[0]['related'][0]['id'], 'lesson:1.1')
        self.assertEqual(result[1]['stored_at'], EVENT['ts'])
        self.assertEqual(self.snapshot(), before)

    def test_expiry_and_natural_language_holds(self):
        event = copy.deepcopy(EVENT)
        self.write('state/lessons.jsonl', json.dumps(event))
        now = dt.datetime(2030, 1, 1, tzinfo=dt.timezone.utc)
        self.assertEqual(reader.read(self.root, now)['records'][0]['status'], 'Recorded')
        event['lesson']['expires_at'] = '2027-01-01T00:00:00Z'
        self.write('state/lessons.jsonl', json.dumps(event))
        self.assertEqual(reader.read(self.root, now)['records'][0]['status'], 'Expired')

    def test_invalid_journal_remains_inspectable(self):
        self.write('profile/qa.md', QA)
        self.write('state/lessons.jsonl', '{invalid')
        result = reader.read(self.root)
        self.assertEqual(len(result['records']), 2)
        self.assertTrue(result['warnings'])
        self.assertEqual(result['records'][1]['locations'][0]['raw'], '{invalid')

    def test_external_symlink_is_not_read(self):
        self.write('outside.md', 'not teaching')
        nested = self.root / 'nested'
        (nested / 'profile').mkdir(parents=True)
        (nested / 'profile/qa.md').symlink_to(self.root / 'outside.md')
        result = reader.read(nested)
        self.assertFalse(result['records'])
        self.assertIn('outside', result['warnings'][0])

    def serve(self):
        httpd = server.make_server(self.root)
        worker = threading.Thread(target=httpd.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(lambda: (httpd.shutdown(), worker.join(), httpd.server_close()))
        self.httpd = httpd
        self.token = urlsplit(httpd.url).fragment.split('=', 1)[1]

    def request(self, path, method='GET', headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.httpd.server_port)
        conn.request(method, path, headers=headers or {'X-Viewer-Token': self.token})
        response = conn.getresponse()
        status, body, response_headers = response.status, response.read(), dict(response.getheaders())
        conn.close()
        return status, body, response_headers

    def test_http_list_detail_refresh_and_no_writes(self):
        self.write('profile/qa.md', QA)
        self.serve()
        before = self.snapshot()
        self.assertEqual(self.httpd.server_address[0], '127.0.0.1')
        status, body, headers = self.request('/api/lessons')
        self.assertEqual(status, 200)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        self.assertEqual(len(json.loads(body)['records']), 1)
        self.assertEqual(self.request('/api/lesson?id=profile/qa.md:Q1')[0], 200)
        self.assertEqual(self.request('/api/lesson?id=missing')[0], 404)
        self.assertEqual(self.request('/api/lessons', 'POST')[0], 501)
        self.assertEqual(self.snapshot(), before)
        self.write('profile/qa.md', QA + '\n## 2. New teaching\nFresh content\n')
        self.assertEqual(len(json.loads(self.request('/api/lessons')[1])['records']), 2)

    def test_server_starts_without_reverse_dns_lookup(self):
        # macOS 15 resolvers stall loopback reverse lookups; startup must never depend on them.
        with patch('socket.getfqdn', side_effect=AssertionError('reverse DNS lookup')), \
                patch('socket.gethostbyaddr', side_effect=AssertionError('reverse DNS lookup')):
            self.serve()
        self.assertEqual((self.httpd.server_name, self.httpd.server_port), ('127.0.0.1', self.httpd.server_address[1]))
        self.assertEqual(self.request('/')[0], 200)

    def test_http_rejects_cross_origin_missing_token_and_arbitrary_files(self):
        self.write('profile/qa.md', QA)
        self.serve()
        for headers in ({'X-Viewer-Token': 'wrong'}, {'Origin': 'https://example.org', 'X-Viewer-Token': self.token},
                        {'Host': 'attacker.test', 'X-Viewer-Token': self.token}):
            self.assertEqual(self.request('/api/lessons', headers=headers)[0], 403)
        for path in ('/profile/qa.md', '/../profile/qa.md', '/%2e%2e/profile/qa.md', '/.env'):
            self.assertEqual(self.request(path)[0], 404)
        for path in ('/', '/app.js', '/styles.css'):
            self.assertEqual(self.request(path)[0], 200)

    def test_cli_uses_explicit_home_without_install_metadata_or_migration(self):
        result = subprocess.run([sys.executable, str(ROOT / 'director/cli.py'), '--home', str(self.root),
                                 'memory', '--port', '-1', '--no-open'], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('port must be', result.stderr)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_packaged_cli_reads_installation_home_not_release_directory(self):
        from scripts.build_release import build_release
        from director.install import extract, validate_source
        output = self.root / 'bundle'
        build_release(ROOT, 'v9.9.9', output)
        release = extract(output / 'director-v9.9.9.tar.gz', self.root / 'unpacked')
        self.assertEqual(validate_source(release), 'v9.9.9')
        home = self.root / 'installation'
        self.write('installation/profile/qa.md', QA)
        before = self.snapshot()
        process = subprocess.Popen([sys.executable, '-B', str(release / 'director/cli.py'),
                                    '--home', str(home), 'memory', '--no-open'],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertTrue(select.select([process.stdout], [], [], 10)[0], 'CLI failed to start')
            url = urlsplit(process.stdout.readline().strip())
            self.assertEqual(url.hostname, '127.0.0.1')
            conn = http.client.HTTPConnection(url.hostname, url.port)
            conn.request('GET', '/api/lessons', headers={'X-Viewer-Token': url.fragment.split('=', 1)[1]})
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read())['records'][0]['title'], 'Diagnose first')
            conn.close()
        finally:
            process.terminate()
            process.communicate(timeout=10)
        self.assertEqual(self.snapshot(), before)


if __name__ == '__main__':
    unittest.main()
