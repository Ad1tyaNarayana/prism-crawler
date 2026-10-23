import subprocess

import pytest
from aiohttp import web

from benchmarks.continuation_fixture import pdf_bytes, site
from benchmarks.fixture import ARTICLE
from optimal_crawler.crawler import Crawler
from optimal_crawler.extract import Extractor
from optimal_crawler.models import CrawlConfig, FetchResult, Strategy
from tests.test_realworld_regressions import serve


@pytest.mark.parametrize('strategy', list(Strategy))
async def test_pdf_content_limits_and_refusals_have_explicit_results(tmp_path, strategy):
    paths = ['/paper.pdf', '/scan.pdf', '/locked.pdf', '/long.pdf', '/malformed.pdf', '/private/paper.pdf']
    async with site() as (base, receipts):
        config = CrawlConfig(db=tmp_path / 'pdf.db', strategy=strategy, allow_private=True,
                             min_delay=0, discover=False, follow_links=False)
        async with Crawler(config) as crawler:
            summary = await crawler.run([base + p for p in paths])
            docs = {d['url'].removeprefix(base): d for d in crawler.store.documents(crawler.run_id)}
    assert set(docs) == set(paths)
    assert docs['/paper.pdf']['outcome'] == 'ok'
    assert 'rare helium signal' in docs['/paper.pdf']['text']
    assert docs['/paper.pdf']['parser'] == 'pdf'
    assert docs['/long.pdf']['outcome'] == 'incomplete'
    assert docs['/long.pdf']['metadata']['pdf']['extracted_pages'] == 100
    assert docs['/long.pdf']['text'] == ''
    assert docs['/scan.pdf']['outcome'] == docs['/locked.pdf']['outcome'] == docs['/malformed.pdf']['outcome'] == 'unsupported'
    assert 'OCR' in docs['/scan.pdf']['reason']
    assert 'Encrypted' in docs['/locked.pdf']['reason']
    assert docs['/private/paper.pdf']['outcome'] == 'robots_denied'
    assert receipts['/private/paper.pdf'] == 0
    assert summary['browser_pages'] == 0


async def test_frontier_follows_public_pdf_links(tmp_path):
    async def handler(request):
        if request.path == '/robots.txt':
            return web.Response(text='User-agent: *\nAllow: /\n')
        if request.path == '/paper.pdf':
            return web.Response(body=pdf_bytes(), content_type='application/octet-stream')
        return web.Response(text=f'<main><p>{ARTICLE}</p><a href="/paper.pdf">Public report</a></main>',
                            content_type='text/html')

    async with serve(handler) as base:
        async with Crawler(CrawlConfig(db=tmp_path / 'links.db', strategy=Strategy.HTTP,
                                       allow_private=True, min_delay=0, discover=False, max_pages=2)) as crawler:
            summary = await crawler.run([base + '/index'])
            docs = crawler.store.documents(crawler.run_id)
    assert summary['outcomes'] == {'ok': 2}
    assert {d['parser'] for d in docs}.intersection({'pdf'}) == {'pdf'}


@pytest.mark.parametrize('strategy', [Strategy.BROWSER, Strategy.ADAPTIVE])
async def test_scroll_recovers_deferred_content_and_stops_continuous_growth(tmp_path, strategy):
    async with site() as (base, receipts):
        config = CrawlConfig(db=tmp_path / 'scroll.db', strategy=strategy, allow_private=True,
                             min_delay=0, discover=False, follow_links=False, browser_timeout=5,
                             render_wait=0.1, scroll_steps=3)
        async with Crawler(config) as crawler:
            await crawler.run([base + '/deferred', base + '/infinite'])
            docs = {d['url'].removeprefix(base): d for d in crawler.store.documents(crawler.run_id)}
    assert docs['/deferred']['outcome'] == 'ok'
    assert 'thirty six maps' in docs['/deferred']['text']
    assert receipts['/chunk'] == 1
    assert docs['/infinite']['outcome'] == 'incomplete'
    assert docs['/infinite']['metadata']['browser']['scroll']['steps'] == 3
    assert docs['/infinite']['metadata']['browser']['scroll']['limit_reached']
    assert docs['/infinite']['text'] == ''


async def test_scroll_triggered_requests_still_obey_robots(tmp_path):
    forbidden = []

    async def handler(request):
        if request.path == '/robots.txt':
            return web.Response(text='User-agent: *\nDisallow: /private/\n')
        if request.path == '/private/records':
            forbidden.append(request.path)
            return web.json_response({'text': ARTICLE})
        return web.Response(text=f'<main><p>{ARTICLE}</p><div style="height:1400px"></div>'
                            '<div aria-busy="true">Loading more entries</div></main>'
                            '<script>window.addEventListener("scroll",()=>fetch("/private/records"),{once:true})</script>',
                            content_type='text/html')

    async with serve(handler) as base:
        async with Crawler(CrawlConfig(db=tmp_path / 'robots-scroll.db', allow_private=True,
                                       min_delay=0, discover=False, follow_links=False,
                                       browser_timeout=4, render_wait=0.1, scroll_steps=2)) as crawler:
            await crawler.run([base + '/report'])
            doc = crawler.store.documents(crawler.run_id)[0]
    assert doc['outcome'] == 'incomplete'
    assert not forbidden
    assert any(r['outcome'] == 'robots_denied' for r in doc['metadata']['browser']['dependency_refusals'])


def test_pdf_worker_timeout_does_not_become_browser_recovery(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 10)

    monkeypatch.setattr(subprocess, 'run', timeout)
    e = Extractor().extract(FetchResult('https://example.org/paper.pdf', body=b'%PDF-1.7',
                                       headers={'content-type': 'application/pdf'}))
    assert e.unsupported and not e.candidates
    assert 'timed out' in e.reason


async def test_credible_content_does_not_wait_for_background_scripts_while_scrolling(tmp_path):
    import asyncio

    release = asyncio.Event()

    async def handler(request):
        if request.path == '/robots.txt':
            return web.Response(text='User-agent: *\nAllow: /\n')
        if request.path == '/background.js':
            await release.wait()
            return web.Response(text='', content_type='application/javascript')
        return web.Response(text=f'<main><h1>Observatory</h1><p>{ARTICLE}</p>'
                            '<div style="height:4000px"></div></main>'
                            '<script async src="/background.js"></script>', content_type='text/html')

    async with serve(handler) as base:
        try:
            config = CrawlConfig(db=tmp_path / 'background.db', strategy=Strategy.BROWSER,
                                 allow_private=True, min_delay=0, discover=False, follow_links=False,
                                 browser_timeout=3, render_wait=0.1, scroll_steps=1)
            async with Crawler(config) as crawler:
                await crawler.run([base + '/article'])
                doc = crawler.store.documents(crawler.run_id)[0]
        finally:
            release.set()
    assert doc['outcome'] == 'ok'
    assert 'rare helium signal' in doc['text']
    assert not doc['metadata']['browser']['deadline_reached']
    assert doc['metadata']['browser']['scroll']['dependency_wait'] == 'content_settling'
