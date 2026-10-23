from aiohttp import web

from optimal_crawler.models import CrawlConfig, Outcome
from optimal_crawler.network import Network
from optimal_crawler.store import Store
from tests.test_realworld_regressions import serve


async def test_explicit_security_checkpoint_is_not_retried_as_rate_limiting(tmp_path):
    paths = []

    async def handler(request):
        paths.append(request.path)
        if request.path == "/robots.txt":
            return web.Response(text="User-agent: *\nAllow: /\n")
        return web.Response(status=429, content_type="text/html",
            text="<title>Vercel Security Checkpoint</title><h1>We're verifying your browser</h1>")

    async with serve(handler) as base:
        config = CrawlConfig(db=tmp_path / "challenge.db", allow_private=True, min_delay=0,
                             retries=2)
        store = Store(config.db)
        run = store.start(config, [base])
        try:
            async with Network(config, store, run, {base}) as network:
                result = await network.fetch(base + "/article")
        finally:
            store.close()
    assert result.outcome == Outcome.BLOCKED
    assert result.status == 429
    assert paths.count("/article") == 1


async def test_ordinary_rate_limit_still_retries_after_server_delay(tmp_path):
    calls = 0

    async def handler(request):
        nonlocal calls
        if request.path == "/robots.txt":
            return web.Response(text="User-agent: *\nAllow: /\n")
        calls += 1
        if calls == 1:
            return web.Response(status=429, text="Too many requests", headers={"Retry-After": "0.1"})
        return web.Response(text="Research report")

    async with serve(handler) as base:
        config = CrawlConfig(db=tmp_path / "retry.db", allow_private=True, min_delay=0, retries=1)
        store = Store(config.db)
        run = store.start(config, [base])
        try:
            async with Network(config, store, run, {base}) as network:
                result = await network.fetch(base + "/article")
        finally:
            store.close()
    assert result.outcome == Outcome.OK
    assert calls == 2
