import asyncio
import time

from aiohttp import web

from optimal_crawler.models import CrawlConfig, Outcome
from optimal_crawler.network import Network
from optimal_crawler.store import Store
from tests.test_realworld_regressions import serve


async def test_assets_overlap_with_a_bounded_origin_limit(tmp_path):
    active, peak = 0, 0

    async def handler(request):
        nonlocal active, peak
        if request.path == "/robots.txt":
            return web.Response(text="User-agent: *\nAllow: /\n")
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.15)
        active -= 1
        return web.Response(text="export default 1", content_type="text/javascript")

    async with serve(handler) as base:
        config = CrawlConfig(db=tmp_path / "assets.db", allow_private=True, min_delay=0.5,
                             asset_concurrency=3, asset_delay=0.01)
        store = Store(config.db)
        run = store.start(config, [base])
        try:
            async with Network(config, store, run, {base}) as network:
                await network.allowed(base)
                start = time.monotonic()
                results = await asyncio.gather(*(network.fetch(base + f"/{i}.js",
                    asset=True, role="browser_asset") for i in range(12)))
                elapsed = time.monotonic() - start
                assert all(r.outcome == Outcome.OK for r in results)
                assert 1 < peak <= 3
                assert elapsed < 3
                assert len(store.events("requests", run)) == 13
        finally:
            store.close()


async def test_robots_delay_applies_across_parallel_asset_and_page_lanes(tmp_path):
    starts = []

    async def handler(request):
        if request.path == "/robots.txt":
            return web.Response(text="User-agent: *\nAllow: /\nCrawl-delay: 0.1\nDisallow: /private\n")
        starts.append(time.monotonic())
        await asyncio.sleep(0.12)
        return web.Response(text="test")

    async with serve(handler) as base:
        config = CrawlConfig(db=tmp_path / "robots.db", allow_private=True, min_delay=0,
                             asset_delay=0, asset_concurrency=4)
        store = Store(config.db)
        run = store.start(config, [base])
        try:
            async with Network(config, store, run, {base}) as network:
                results = await asyncio.gather(*(network.fetch(base + f"/{i}", asset=i != 0,
                    role="browser_asset" if i else "http") for i in range(6)))
                denied = await network.fetch(base + "/private", asset=True, role="browser_asset")
                assert denied.outcome == Outcome.ROBOTS
                assert all(r.outcome == Outcome.OK for r in results)
                assert len(starts) == 6
                assert all(b - a >= 0.085 for a, b in zip(starts, starts[1:], strict=False))
        finally:
            store.close()


async def test_asset_retry_after_cannot_be_cleared_by_success_in_other_lane(tmp_path):
    starts = []

    async def handler(request):
        if request.path == "/robots.txt":
            return web.Response(text="User-agent: *\nAllow: /\n")
        starts.append((request.path, time.monotonic()))
        if request.path == "/limited":
            return web.Response(status=429, headers={"Retry-After": "0.2"})
        return web.Response(text="ok")

    async with serve(handler) as base:
        config = CrawlConfig(db=tmp_path / "backoff.db", allow_private=True, min_delay=0,
                             asset_delay=0, retries=0)
        store = Store(config.db)
        run = store.start(config, [base])
        try:
            async with Network(config, store, run, {base}) as network:
                await network.fetch(base + "/limited", asset=True, role="browser_asset")
                await asyncio.gather(network.fetch(base + "/page"), network.fetch(
                    base + "/asset", asset=True, role="browser_asset"))
                assert min(t for path, t in starts if path != "/limited") - starts[0][1] >= 0.18
        finally:
            store.close()
