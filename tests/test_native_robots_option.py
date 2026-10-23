from aiohttp import web

from experiments.native_retrieval import NativeRetriever
from optimal_crawler.models import CrawlConfig, Outcome
from tests.test_realworld_regressions import serve


async def test_explicit_robots_override_skips_policy_but_preserves_scope_and_access_status():
    paths = []

    async def handler(request):
        paths.append(request.path)
        if request.path == '/robots.txt':
            return web.Response(text='User-agent: *\nDisallow: /\n')
        return web.Response(status=403, content_type='text/html',
                            text='<title>Client Challenge</title><script>fetch("/unexpected")</script>')

    async with serve(handler) as base:
        config = CrawlConfig(allow_private=True, min_delay=0, timeout=2)
        async with NativeRetriever(config, {base}, ignore_robots=True) as native:
            outside, _ = await native.policy('https://example.invalid/page')
            result = await native.fetch(base + '/page')
    assert outside == Outcome.OUT_OF_SCOPE
    assert result.outcome == Outcome.BLOCKED
    assert result.status == 403
    assert paths == ['/page']


async def test_default_still_enforces_robots():
    paths = []

    async def handler(request):
        paths.append(request.path)
        return web.Response(text='User-agent: *\nDisallow: /\n')

    async with serve(handler) as base:
        async with NativeRetriever(CrawlConfig(allow_private=True, min_delay=0), {base}) as native:
            result = await native.fetch(base + '/page')
    assert result.outcome == Outcome.ROBOTS
    assert paths == ['/robots.txt']
