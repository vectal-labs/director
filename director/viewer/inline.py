"""Export the shared teaching UI as a self-contained bb inline snapshot."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import uuid

from .reader import read


def render(root):
    snapshot = read(root)
    snapshot['captured_at'] = dt.datetime.now().astimezone().isoformat(timespec='seconds')
    data = json.dumps(snapshot, ensure_ascii=False).replace('<', '\\u003c')
    assets = Path(__file__).parent
    html = (assets / 'index.html').read_text()
    policy = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"
    html = html.replace('<head>', '<head>\n<meta http-equiv="Content-Security-Policy" content="' + policy + '">')
    html = html.replace('<link rel="stylesheet" href="/styles.css">', '<style>' + (assets / 'styles.css').read_text() + '\nbody { padding: 0; }\n</style>')
    html = html.replace('<script src="/app.js" defer></script>', '')
    scripts = '<script type="application/json" id="teaching-snapshot">' + data + '</script>\n'
    scripts += '<script>' + (assets / 'app.js').read_text() + '</script>'
    html = html.replace('</body>', scripts + '\n</body>')
    content = html.encode('utf-8')
    if len(content) >= 5 * 1024 * 1024:
        raise ValueError('Snapshot exceeds the bb inline 5 MiB limit; use director memory for this profile.')
    return content


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--output', type=Path, default=Path('private') / ('teachings-' + uuid.uuid4().hex[:12] + '.html'))
    args = parser.parse_args(argv)
    output = args.output.absolute()
    try:
        relative = output.relative_to(Path.cwd())
        if not relative.parts or relative.parts[0] != 'private' or output.suffix != '.html':
            raise ValueError('Output must be an .html file under the current workspace private/ directory')
        if any(char in str(relative) for char in ('"', '\n', '\r')):
            raise ValueError('Output path cannot contain quotes or line breaks')
        if not output.resolve().is_relative_to((Path.cwd() / 'private').absolute()):
            raise ValueError('Output must not follow a symlink outside workspace private/')
        content = render(args.root)
        output.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as file:
            file.write(content)
    except (OSError, ValueError) as error:
        parser.exit(1, f'Inline teaching viewer: {error}\n')
    print(f'::inline-vis{{file="{relative.as_posix()}" height="960"}}')


if __name__ == '__main__':
    main()
