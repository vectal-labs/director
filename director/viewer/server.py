"""Serve the teaching viewer on loopback, without exposing arbitrary files."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import webbrowser
from urllib.parse import parse_qs, urlsplit

from . import reader

ASSETS = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'),
          '/styles.css': ('styles.css', 'text/css')}


def make_server(root, port=0):
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError('Profile root does not exist')
    token = secrets.token_urlsafe(24)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            # URLs contain a local session token; teaching never enters access logs.
            return

        def send(self, status, body, mime='text/plain'):
            self.send_response(status)
            self.send_header('Content-Type', mime + '; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            host = f'127.0.0.1:{self.server.server_port}'
            if self.headers.get('Host') != host or self.headers.get('Origin') not in (None, 'http://' + host):
                return self.send(403, b'Forbidden')
            request = urlsplit(self.path)
            if request.path in ASSETS:
                name, mime = ASSETS[request.path]
                return self.send(200, (Path(__file__).parent / name).read_bytes(), mime)
            if request.path not in ('/api/lessons', '/api/lesson'):
                return self.send(404, b'Not found')
            if not secrets.compare_digest(self.headers.get('X-Viewer-Token', ''), token):
                return self.send(403, b'Forbidden')
            try:
                snapshot = reader.read(root)
                if request.path == '/api/lesson':
                    identity = parse_qs(request.query).get('id', [''])[0]
                    result = next((r for r in snapshot['records'] if r['id'] == identity), None)
                    if result is None:
                        return self.send(404, b'Teaching no longer exists. Refresh the list.')
                else:
                    result = dict(warnings=snapshot['warnings'], records=[
                        {key: row[key] for key in ('id', 'title', 'kind', 'source_date', 'stored_at', 'status')}
                        for row in snapshot['records']])
                self.send(200, json.dumps(result, ensure_ascii=False).encode(), 'application/json')
            except (ValueError, OSError) as error:
                self.send(500, str(error).encode())

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    server.url = f'http://127.0.0.1:{server.server_port}/#token={token}'
    return server


def main(argv=None, root=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=root or Path(__file__).resolve().parents[2], help='checkout or installation containing profile/ and state/')
    parser.add_argument('--port', type=int, default=0, help='local port; default selects a free port')
    parser.add_argument('--no-open', action='store_true', help='print the URL without opening a browser')
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error('port must be between 0 and 65535')
    try:
        with make_server(args.root, args.port) as server:
            print(server.url, flush=True)
            print('Read-only memory viewer. Press Ctrl+C to stop.', flush=True)
            if not args.no_open:
                webbrowser.open(server.url)
            server.serve_forever()
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError) as error:
        parser.exit(1, f'Memory viewer: {error}\n')
    return 0


if __name__ == '__main__':
    main()
