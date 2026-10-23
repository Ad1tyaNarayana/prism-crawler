import asyncio

from benchmarks.fixture import site
from optimal_crawler.models import CrawlConfig, Outcome
from optimal_crawler.network import Network
from optimal_crawler.store import Store


async def test_robots_redirect_retry_and_byte_limits(tmp_path):
    async with site() as fixture:
        config = CrawlConfig(db=tmp_path / "network.db", allow_private=True, min_delay=0.01,
                             max_bytes=200_000, timeout=3)
        store = Store(config.db)
        run = store.start(config, [fixture.base])
        async with Network(config, store, run, {fixture.base}) as network:
            assert (await network.fetch(fixture.base + "/private/secret")).outcome == Outcome.ROBOTS
            assert (await network.fetch(fixture.base + "/private/public")).outcome == Outcome.OK
            assert (await network.fetch(fixture.base + "/redirect-private")).outcome == Outcome.ROBOTS
            assert fixture.counts["/private/secret"] == 0
            assert (await network.fetch(fixture.base + "/huge")).outcome == Outcome.UNSUPPORTED
            assert (await network.fetch(fixture.base + "/flaky")).outcome == Outcome.OK
            times = [r["time"] for r in fixture.receipts if r["path"] == "/flaky"]
            assert len(times) == 2 and times[1] - times[0] >= 0.09
            loop = await network.fetch(fixture.base + "/redirect-loop")
            assert loop.outcome == Outcome.ERROR
            assert loop.reason == "Redirect loop detected"
            assert fixture.counts["/redirect-loop"] == 1
        store.close()


async def test_private_network_and_unlisted_origins_denied(tmp_path):
    config = CrawlConfig(db=tmp_path / "network.db", min_delay=0)
    store = Store(config.db)
    run = store.start(config, ["http://127.0.0.1:1"])
    async with Network(config, store, run, {"http://127.0.0.1:1"}) as network:
        assert (await network.fetch("http://127.0.0.1:1/")).outcome == Outcome.OUT_OF_SCOPE
        assert (await network.fetch("https://example.org/")).outcome == Outcome.OUT_OF_SCOPE
        assert network.request_count == 0
    store.close()


async def test_global_budget_is_atomic_under_concurrency(tmp_path):
    async with site() as fixture:
        config = CrawlConfig(db=tmp_path / "budget.db", allow_private=True, min_delay=0,
                             max_requests=3, retries=0)
        store = Store(config.db)
        run = store.start(config, [fixture.base])
        async with Network(config, store, run, {fixture.base}) as network:
            results = await asyncio.gather(*(network.fetch(fixture.base + "/static") for _ in range(10)))
            assert len(fixture.receipts) == 3
            assert sum(r.outcome == Outcome.BUDGET for r in results) == 8
        store.close()
