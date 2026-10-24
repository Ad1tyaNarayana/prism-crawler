"""Check relative Markdown links in current public documentation."""

import re
from pathlib import Path
from urllib.parse import unquote

FILES = [Path(name) for name in (
    'README.md', 'CONTRIBUTING.md', 'SECURITY.md', 'CODE_OF_CONDUCT.md', 'CHANGELOG.md',
    'THIRD_PARTY_NOTICES.md', 'docs/api.md', 'docs/architecture.md', 'docs/limits.md',
    'docs/usage.md', 'docs/releases.md', 'docs/provenance.md', 'docs/evaluation.md',
    'docs/comparison.md', 'docs/oss-benchmark-method.md', 'docs/oss-benchmark-results.md',
)]


def main():
    missing = []
    for file in FILES:
        for link in re.findall(r'\[[^\]]*\]\(([^)]+)\)', file.read_text()):
            link = unquote(link.split('#')[0].split(' "')[0])
            if not link or '://' in link or link.startswith('mailto:'):
                continue
            if not (file.parent/link).exists():
                missing.append(f'{file}: {link}')
    if missing:
        raise SystemExit('\n'.join(missing))
    print(f'Relative links checked in {len(FILES)} current documentation files.')


if __name__ == '__main__':
    main()
