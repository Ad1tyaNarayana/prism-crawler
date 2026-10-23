import asyncio
from dataclasses import replace

import pytest

from benchmarks.fixture import ARTICLE, site
from optimal_crawler.crawler import Crawler
from optimal_crawler.models import CrawlConfig, Strategy
from optimal_crawler.store import Store


def config_at(tmp_path, **kwargs):
    return CrawlConfig(db=tmp_path / "crawl.db", allow_private=True, min_delay=0,
                       discover=False, follow_links=False, render_wait=0.7, **kwargs)


async def test_adaptive_recovers_javascript_and_hydration(tmp_path):
    async with site() as fixture:
        config = config_at(tmp_path)
        async with Crawler(config) as crawler:
            result = await crawler.run([fixture.base + p for p in ["/static", "/js", "/hydration", "/api-shell"]])
            docs = crawler.store.documents(crawler.run_id)
            assert result["outcomes"] == {"ok": 4}
            assert result["browser_pages"] == 1
            assert {d["method"] for d in docs} == {"http", "browser", "api"}
            assert all("BOILERPLATE_SENTINEL" not in d["text"] for d in docs)


async def test_learning_skips_failed_http_for_same_template(tmp_path):
    async with site() as fixture:
        async with Crawler(config_at(tmp_path)) as crawler:
            await crawler.run([fixture.base + f"/spa/{i}" for i in range(1, 4)])
            docs = crawler.store.documents(crawler.run_id)
            assert docs[-1]["metadata"]["route"] == "learned"
            assert all(d["outcome"] == "ok" for d in docs)
            assert fixture.counts["/spa/1"] == fixture.counts["/spa/2"] == 2
            assert fixture.counts["/spa/3"] == 1


async def test_revalidation_preserves_content_and_adapts_schedule(tmp_path):
    async with site() as fixture:
        config = config_at(tmp_path)
        snapshots = []
        for version in (1, 1, 2):
            fixture.version = version
            async with Crawler(config) as crawler:
                await crawler.run([fixture.base + "/etag"])
                snapshots.append(crawler.store.documents(crawler.run_id)[0])
        assert [d["outcome"] for d in snapshots] == ["ok", "unchanged", "ok"]
        assert ARTICLE in snapshots[1]["text"]
        assert "Revision 2" in snapshots[2]["text"]
        assert snapshots[1]["next_due"] > snapshots[0]["next_due"] > snapshots[2]["next_due"]


async def test_browser_cannot_bypass_network_policy(tmp_path):
    async with site() as fixture:
        async with Crawler(config_at(tmp_path, strategy=Strategy.BROWSER)) as crawler:
            summary = await crawler.run([fixture.base + "/hostile"])
            assert summary["outcomes"] == {"ok": 1}
            assert fixture.counts["/private/secret"] == fixture.counts["/mutate"] == 0
            requests = crawler.store.events("requests", crawler.run_id)
            assert all(r["url"].startswith(fixture.base) for r in requests)


async def test_browser_drains_asset_tasks_before_storage_closes(tmp_path):
    errors = []
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _, context: errors.append(context))
    try:
        async with site() as fixture:
            async with Crawler(config_at(tmp_path, strategy=Strategy.BROWSER)) as crawler:
                summary = await crawler.run([fixture.base + "/slow-assets"])
                assert summary["outcomes"] == {"ok": 1}
                requests = crawler.store.events("requests", crawler.run_id)
                slow = next(r for r in requests if r["url"].endswith("/slow-asset"))
                assert slow["reason"] == "Request cancelled"
                assert len(requests) == len(fixture.receipts)
            await asyncio.sleep(0.05)
            assert not errors
    finally:
        loop.set_exception_handler(previous_handler)


async def test_resume_after_actual_cancellation(tmp_path):
    async with site() as fixture:
        config = config_at(tmp_path)
        seeds = [fixture.base + "/slow", fixture.base + "/static"]
        async with Crawler(config) as crawler:
            task = asyncio.create_task(crawler.run(seeds))
            while not fixture.counts["/slow"]:
                await asyncio.sleep(0.01)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            run_id = crawler.run_id
        async with Crawler(config) as crawler:
            result = await crawler.run(seeds, resume=True)
            assert crawler.run_id == run_id
            assert result["outcomes"] == {"ok": 2}
            assert result["pending"] == 0


async def test_scope_checked_after_every_redirect(tmp_path):
    async with site() as first, site() as second:
        for strategy in (Strategy.HTTP, Strategy.BROWSER):
            config = replace(config_at(tmp_path), strategy=strategy,
                             db=tmp_path / f"{strategy}.db")
            async with Crawler(config) as crawler:
                summary = await crawler.run([first.base + "/redirect-away?to=" + second.base + "/static"])
                assert summary["outcomes"] == {"out_of_scope": 1}
            assert not second.receipts


async def test_browser_redirect_preserves_relative_script_paths(tmp_path):
    async with site() as fixture:
        async with Crawler(config_at(tmp_path, strategy=Strategy.BROWSER)) as crawler:
            summary = await crawler.run([fixture.base + "/redirect-nested"])
            document = crawler.store.documents(crawler.run_id)[0]
            assert summary["outcomes"] == {"ok": 1}
            assert document["final_url"] == fixture.base + "/nested/index"
            assert ARTICLE in document["text"]
            assert fixture.counts["/nested/render.js"] == 1


async def test_sitemaps_precede_content_and_frontier_is_bounded(tmp_path):
    async with site() as fixture:
        config = replace(config_at(tmp_path), discover=True, max_pages=3, max_frontier=5)
        async with Crawler(config) as crawler:
            summary = await crawler.run([fixture.base + "/static?utm_source=test"])
            assert summary["pages"] == 3
            assert summary["pending"] == 2
            assert [r["path"] for r in fixture.receipts[:4]] == ["/robots.txt", "/sitemap.xml", "/child.xml", "/static"]


async def test_one_process_owns_each_database(tmp_path):
    config = config_at(tmp_path)
    async with Crawler(config), Crawler(replace(config, db=tmp_path / "other.db")):
        with pytest.raises(RuntimeError, match="Another crawler"):
            async with Crawler(config):
                pass


def test_schedule_survives_process_close(tmp_path):
    store = Store(tmp_path / "crawl.db")
    assert store.due() == []
    store.close()
