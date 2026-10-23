import asyncio

import pytest
from aiohttp import web

from experiments.native_retrieval import NativeRetriever
from optimal_crawler.models import CrawlConfig, Outcome
from tests.test_realworld_regressions import serve


@pytest.mark.parametrize('status,expected', [(200, Outcome.OK), (503, Outcome.ROBOTS_UNAVAILABLE),
                                          (522, Outcome.ROBOTS_UNAVAILABLE)])
async def test_concurrent_policy_checks_share_one_request(status, expected):
    paths = []

    async def handler(request):
        paths.append(request.path)
        await asyncio.sleep(.15)
        return web.Response(status=status, text='User-agent: *\nAllow: /\n')

    async with serve(handler) as base:
        config = CrawlConfig(allow_private=True, min_delay=0, timeout=2)
        async with NativeRetriever(config, {base}) as native:
            results = await asyncio.gather(*(native.policy(base + '/page') for _ in range(4)))
            diagnostics = [e for e in native.events if e['type'] == 'navigation']
    assert all(outcome == expected for outcome, _ in results)
    assert paths == ['/robots.txt']
    assert diagnostics[0]['phase'] == 'response_body_complete'


@pytest.mark.parametrize('partial_body,phase', [(False, 'awaiting_response_headers'),
                                               (True, 'reading_response_body')])
async def test_robots_timeout_reports_stalled_phase_and_denies_content(partial_body, phase):
    paths = []

    async def handler(request):
        paths.append(request.path)
        if partial_body:
            response = web.StreamResponse(headers={'Content-Length': '4096',
                                                   'Content-Type': 'text/plain',
                                                   'X-Content-Type-Options': 'nosniff'})
            await response.prepare(request)
            await response.write(b'User-agent: *\n' + b'# padding\n' * 110)
            await asyncio.sleep(.8)
            return response
        await asyncio.sleep(.8)
        return web.Response(text='User-agent: *\nAllow: /\n')

    async with serve(handler) as base:
        config = CrawlConfig(allow_private=True, min_delay=0, timeout=.3)
        async with NativeRetriever(config, {base}) as native:
            result = await native.fetch(base + '/page')
            diagnostics = [e for e in native.events if e['type'] == 'navigation']
    assert result.outcome == Outcome.ROBOTS_UNAVAILABLE
    assert paths == ['/robots.txt']
    assert diagnostics[0]['phase'] == phase
    assert diagnostics[0]['response_status'] == (200 if partial_body else None)
    assert phase.replace('_', ' ') in result.reason


async def test_connection_failure_does_not_claim_response_headers():
    async def handler(request):
        request.transport.abort()
        return web.Response()

    async with serve(handler) as base:
        config = CrawlConfig(allow_private=True, min_delay=0, timeout=2)
        async with NativeRetriever(config, {base}) as native:
            result = await native.navigate(base + '/robots.txt', robots=True)
    assert result.outcome == Outcome.ERROR
    diagnostic = result.metadata['navigation']
    assert diagnostic['response_status'] is None
    assert all(p['phase'] != 'response_headers_received' for p in diagnostic['phases'])
