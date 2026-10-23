"""One live development run per strategy on Exa pages and public text PDFs."""
import argparse
import asyncio
import gzip
import hashlib
import json
import shutil
import time
from importlib.metadata import version
from pathlib import Path
from unittest.mock import patch

from benchmarks.real50 import SOURCE_ROOT, SiteCase, runtime_digest, score
from optimal_crawler.browser import Browser
from optimal_crawler.crawler import Crawler
from optimal_crawler.models import CrawlConfig, Strategy
from optimal_crawler.network import Network


async def run(output: Path):
    output.mkdir(exist_ok=False)
    shutil.copytree(SOURCE_ROOT, output / 'source', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copyfile(__file__, output / 'harness.py')
    bodies = output / 'bodies'
    bodies.mkdir()
    cases = json.loads(Path('benchmarks/exa.json').read_text()) + [
        {'id': 'attention-pdf', 'category': 'pdf', 'url': 'https://arxiv.org/pdf/1706.03762',
         'min_chars': 2000, 'groups': [['attention'], ['transformer'], ['sequence']]},
        {'id': 'bert-pdf', 'category': 'pdf', 'url': 'https://arxiv.org/pdf/1810.04805',
         'min_chars': 2000, 'groups': [['bert'], ['bidirectional'], ['language']]},
    ]
    (output / 'manifest.json').write_text(json.dumps(cases, indent=2))
    cases = [SiteCase(**row) for row in cases]
    digest = runtime_digest()
    signature = {'runtime_sha256': digest, 'repetitions': 1, 'kind': 'live_development_rerun',
                 'harness_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                 'manifest_sha256': hashlib.sha256((output / 'manifest.json').read_bytes()).hexdigest(),
                 'dependencies': {name: version(name) for name in ['pypdf', 'aiohttp', 'playwright']}}
    (output / 'phase.json').write_text(json.dumps(signature, indent=2))
    original_raw, original_browser = Network._raw, Browser.fetch
    for strategy in Strategy:
        assert digest == runtime_digest(), 'Runtime changed during frozen run'
        captures = []

        def capture(result, role, captures=captures):
            sha = hashlib.sha256(result.body).hexdigest() if result.body else None
            if sha:
                (bodies / (sha + '.gz')).write_bytes(gzip.compress(result.body, mtime=0))
            captures.append({'url': result.url, 'role': role, 'status': result.status,
                             'outcome': result.outcome, 'headers': result.headers,
                             'body_sha256': sha, 'reason': result.reason})

        async def raw(network, url, headers, role, parent, asset=False, *, method='GET'):
            result = await original_raw(network, url, headers, role, parent, asset, method=method)
            if role != 'browser_asset':
                capture(result, role)
            return result

        async def browser(instance, url):
            result = await original_browser(instance, url)
            capture(result, 'rendered_dom')
            return result

        config = CrawlConfig(db=output / f'{strategy}.db', strategy=strategy, max_pages=len(cases),
                             max_requests=500, workers=4, browser_concurrency=4,
                             discover=False, follow_links=False, min_delay=0.5, timeout=15,
                             retries=1, render_wait=3, run_timeout=600)
        print(json.dumps({'event': 'start', 'strategy': strategy}), flush=True)
        with patch.object(Network, '_raw', raw), patch.object(Browser, 'fetch', browser):
            async with Crawler(config) as crawler:
                summary = await crawler.run([case.url for case in cases])
                docs = crawler.store.documents(crawler.run_id)
                result = {**signature, 'strategy': strategy, 'summary': summary,
                          'config': config.to_dict(), 'documents': docs, 'checks': score(cases, docs),
                          'requests': crawler.store.events('requests', crawler.run_id),
                          'attempts': crawler.store.events('attempts', crawler.run_id),
                          'captures': captures, 'finished_at_unix': time.time()}
        assert digest == runtime_digest()
        (output / f'{strategy}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps({'event': 'finished', 'strategy': strategy,
                          'passed': sum(c['passed'] for c in result['checks']),
                          'checks': [{k: c[k] for k in ['id', 'outcome', 'passed', 'chars', 'reason']}
                                     for c in result['checks']]}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('reports/continuation/supplemental'))
    asyncio.run(run(parser.parse_args().output))
