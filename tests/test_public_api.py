import asyncio
import json
from dataclasses import replace

import pytest

from benchmarks.fixture import ARTICLE, SECOND, site
from prism_crawler import AsyncCrawler, CrawlConfig, CrawlResult, CrawlStateError, Outcome


def config_at(tmp_path, **kwargs):
    return CrawlConfig(db=tmp_path / 'library.db', allow_private=True, min_delay=0,
                       discover=False, follow_links=False, render_wait=.2, **kwargs)


async def test_results_survive_context_close_and_export_failures(tmp_path):
    async with site() as fixture:
        async with AsyncCrawler(config_at(tmp_path)) as crawler:
            result = await crawler.crawl([fixture.base+'/static', fixture.base+'/private/secret'])
        assert isinstance(result, CrawlResult)
        assert len(result.documents) == 2
        assert ARTICLE in result.successful[0].text
        assert result.failures[0].outcome is Outcome.ROBOTS
        assert fixture.counts['/private/secret'] == 0
        assert result.summary['outcomes'] == {'ok': 1, 'robots_denied': 1}
        output = result.to_jsonl(tmp_path/'nested'/'pages.jsonl')
        records = [json.loads(line) for line in output.read_text().splitlines()]
        assert {r['outcome'] for r in records} == {'ok', 'robots_denied'}
        assert len(json.loads(json.dumps(result.to_dict()))['documents']) == 2


async def test_single_string_seed_and_javascript_fallback(tmp_path):
    async with site() as fixture:
        async with AsyncCrawler(config_at(tmp_path)) as crawler:
            result = await crawler.crawl(fixture.base+'/js')
        assert result.summary['browser_pages'] == 1
        assert result.successful[0].method == 'browser'
        assert SECOND in result.successful[0].text


async def test_lifecycle_and_overlapping_runs_are_rejected(tmp_path):
    crawler = AsyncCrawler(config_at(tmp_path))
    with pytest.raises(CrawlStateError, match='async with'):
        await crawler.crawl('https://example.org')
    async with site() as fixture:
        async with crawler:
            task = asyncio.create_task(crawler.crawl(fixture.base+'/slow'))
            while not fixture.counts['/slow']:
                await asyncio.sleep(.01)
            with pytest.raises(CrawlStateError, match='each crawl'):
                await crawler.crawl(fixture.base+'/static')
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        with pytest.raises(CrawlStateError):
            await crawler.__aenter__()
        with pytest.raises(CrawlStateError):
            await crawler.crawl(fixture.base+'/static')


async def test_context_exit_cancels_active_task_and_releases_database(tmp_path):
    config = config_at(tmp_path)
    async with site() as fixture:
        seeds = [fixture.base+'/slow', fixture.base+'/static']
        async with AsyncCrawler(config) as crawler:
            task = asyncio.create_task(crawler.crawl(seeds))
            while not fixture.counts['/slow']:
                await asyncio.sleep(.01)
        assert task.cancelled()
        async with AsyncCrawler(config) as resumed:
            result = await resumed.crawl(seeds, resume=True)
        assert len(result.successful) == 2
        assert result.summary['status'] == 'completed'


async def test_invalid_seed_and_lock_failures_are_explicit(tmp_path):
    config = config_at(tmp_path)
    async with AsyncCrawler(config) as crawler:
        with pytest.raises(ValueError):
            await crawler.crawl([])
        with pytest.raises(ValueError):
            await crawler.crawl([42])
        with pytest.raises(ValueError):
            await crawler.crawl('file:///etc/passwd')
        with pytest.raises(RuntimeError, match='owns this database'):
            async with AsyncCrawler(config):
                pass
    async with AsyncCrawler(replace(config, db=tmp_path/'new.db')):
        pass
