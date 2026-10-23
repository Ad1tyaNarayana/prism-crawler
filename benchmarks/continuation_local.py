"""Run the actual CLI on local failures, preserving source and server receipts."""
import argparse
import asyncio
import hashlib
import json
import shutil
import sys
from pathlib import Path

from benchmarks.continuation_fixture import site
from benchmarks.real50 import SOURCE_ROOT, runtime_digest
from optimal_crawler.models import Strategy

PATHS = ['/static', '/deferred', '/infinite', '/paper.pdf', '/scan.pdf', '/locked.pdf',
         '/long.pdf', '/malformed.pdf', '/private/paper.pdf', '/wall']


async def run(output):
    output.mkdir(parents=True, exist_ok=False)
    snapshot = output / 'source'
    snapshot.mkdir()
    for source in SOURCE_ROOT.glob('*.py'):
        shutil.copy2(source, snapshot / source.name)
    records = []
    for strategy in Strategy:
        async with site() as (base, receipts):
            db = output / f'{strategy}.db'
            args = [sys.executable, '-m', 'optimal_crawler.cli', 'crawl', *[base + p for p in PATHS],
                    '--db', str(db), '--strategy', strategy, '--allow-private', '--no-discover',
                    '--no-follow-links', '--delay', '0', '--render-wait', '0.2', '--browser-timeout', '6',
                    '--run-timeout', '40', '--workers', '2']
            process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                         stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await process.communicate()
            (output / f'{strategy}.stdout.json').write_bytes(stdout)
            (output / f'{strategy}.stderr.txt').write_bytes(stderr)
            process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'optimal_crawler.cli',
                'export', '--db', str(db), '--output', str(output / f'{strategy}.jsonl'),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            await process.communicate()
            docs = [json.loads(line) for line in (output / f'{strategy}.jsonl').read_text().splitlines()]
            by_path = {d['url'].removeprefix(base): d for d in docs}
            checks = {}
            for path in PATHS:
                d = by_path.get(path, {})
                if path in {'/static', '/paper.pdf'}:
                    passed = d.get('outcome') == 'ok' and 'rare helium signal' in d.get('text', '')
                elif path == '/deferred':
                    passed = d.get('outcome') == 'ok' and 'thirty six maps' in d.get('text', '')
                elif path in {'/infinite', '/long.pdf'}:
                    passed = d.get('outcome') == 'incomplete' and not d.get('text')
                elif path.startswith('/private'):
                    passed = d.get('outcome') == 'robots_denied' and receipts[path] == 0
                elif path == '/wall':
                    passed = d.get('outcome') == 'access_wall'
                else:
                    passed = d.get('outcome') == 'unsupported'
                checks[path] = {'passed': passed, 'outcome': d.get('outcome', 'not_enqueued'),
                                'method': d.get('method'), 'reason': d.get('reason')}
            record = {'strategy': strategy, 'checks': checks, 'receipts': dict(receipts),
                      'browser_pages': json.loads(stdout)['browser_pages']}
            records.append(record)
            print(json.dumps(record), flush=True)
    fixture = Path('benchmarks/continuation_fixture.py')
    result = {'kind': 'controlled_local_cli', 'runtime_sha256': runtime_digest(),
              'fixture_sha256': hashlib.sha256(fixture.read_bytes()).hexdigest(), 'records': records}
    (output / 'results.json').write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(run(parser.parse_args().output))
