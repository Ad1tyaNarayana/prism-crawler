import asyncio
import time
from dataclasses import replace

import pytest
from aiohttp import web

from benchmarks.adaptive_focus import select_cases
from benchmarks.fixture import ARTICLE
from optimal_crawler.crawler import Crawler
from optimal_crawler.models import CrawlConfig, Outcome
from tests.test_realworld_regressions import serve


def test_benchmark_subset_preserves_original_cases():
    cases = [{"id": "a", "url": "https://example.com/a", "groups": [["original"]]},
             {"id": "b", "url": "https://example.com/b", "groups": [["unchanged"]]}]
    assert select_cases(cases, ["b", "b"]) == [cases[1]]
    assert select_cases(cases, None) is cases
    with pytest.raises(ValueError, match="Unknown case IDs: typo"):
        select_cases(cases, ["typo"])


def config(path, **kwargs):
    return CrawlConfig(
        db=path,
        allow_private=True,
        min_delay=0,
        discover=False,
        follow_links=False,
        retries=1,
        **kwargs,
    )


async def test_transient_robots_failure_retries_after_required_delay(tmp_path):
    robots, pages = [], []

    async def handler(request):
        if request.path == "/robots.txt":
            robots.append(time.monotonic())
            if len(robots) == 1:
                return web.Response(status=429, headers={"Retry-After": "0.12"})
            return web.Response(text="User-agent: *\nAllow: /\n")
        pages.append(request.path)
        return web.Response(text=f"<main><p>{ARTICLE}</p></main>", content_type="text/html")

    async with serve(handler) as base:
        async with Crawler(config(tmp_path / "retry.db")) as crawler:
            summary = await crawler.run([base + "/article"])
    assert summary["outcomes"] == {"ok": 1}
    assert len(robots) == 2 and robots[1] - robots[0] >= 0.12
    assert pages == ["/article"]


@pytest.mark.parametrize("status", [401, 403])
async def test_unreadable_robots_is_not_misreported_as_a_parsed_disallow(tmp_path, status):
    paths = []

    async def handler(request):
        paths.append(request.path)
        return web.Response(status=status)

    async with serve(handler) as base:
        async with Crawler(config(tmp_path / "unavailable.db")) as crawler:
            summary = await crawler.run([base + "/article"])
            doc = crawler.store.documents(crawler.run_id)[0]
    assert summary["outcomes"] == {"robots_unavailable": 1}
    assert str(status) in doc["reason"]
    assert paths == ["/robots.txt"]


async def test_persistently_rate_limited_robots_never_fetches_content(tmp_path):
    paths = []

    async def handler(request):
        paths.append(request.path)
        return web.Response(status=429, headers={"Retry-After": "0.01"})

    async with serve(handler) as base:
        async with Crawler(config(tmp_path / "limited.db")) as crawler:
            summary = await crawler.run([base + "/article"])
    assert summary["outcomes"] == {"robots_unavailable": 1}
    assert paths == ["/robots.txt", "/robots.txt"]


@pytest.mark.parametrize("authorize,deny", [(False, False), (True, False), (True, True)])
async def test_public_redirect_helpers_need_explicit_authorization_and_robots(
    tmp_path, authorize, deny
):
    receipts = []
    first = ""

    async def helper(request):
        receipts.append(request.path)
        if request.path == "/robots.txt":
            return web.Response(
                text="User-agent: *\nDisallow: /transit\n" if deny else "User-agent: *\nAllow: /\n"
            )
        raise web.HTTPFound(first + "/canonical")

    async with serve(helper) as second:

        async def content(request):
            if request.path == "/robots.txt":
                return web.Response(text="User-agent: *\nAllow: /\n")
            if request.path == "/original":
                raise web.HTTPFound(second + "/transit")
            return web.Response(
                text=f"<main><h1>Observatory report</h1><p>{ARTICLE}</p>"
                f'<a href="{second}/not-a-seed">Helper</a></main>',
                content_type="text/html",
            )

        async with serve(content) as first:
            cfg = replace(
                config(tmp_path / "helper.db"),
                follow_links=True,
                max_pages=4,
                redirect_origins=(second,) if authorize else (),
            )
            async with Crawler(cfg) as crawler:
                summary = await crawler.run([first + "/original"])
                doc = crawler.store.documents(crawler.run_id)[0]
                direct = await crawler.network.fetch(second + "/not-a-seed")
    if not authorize:
        assert summary["outcomes"] == {"out_of_scope": 1}
        assert receipts == []
    elif deny:
        assert summary["outcomes"] == {"robots_denied": 1}
        assert receipts == ["/robots.txt"]
    else:
        assert summary["outcomes"] == {"ok": 1}
        assert "rare helium signal" in doc["text"]
        assert doc["final_url"] == first + "/canonical"
        assert receipts == ["/robots.txt", "/transit"]
    assert direct.outcome == Outcome.OUT_OF_SCOPE


async def test_source_title_is_preserved_without_inflating_quality_or_duplicate_headings(tmp_path):
    async def handler(request):
        if request.path == "/robots.txt":
            return web.Response(text="User-agent: *\nAllow: /\n")
        return web.Response(
            text=f"<title>Observatory public forecast</title><main><p>{ARTICLE}</p></main>",
            content_type="text/html",
        )

    async with serve(handler) as base:
        async with Crawler(config(tmp_path / "heading.db")) as crawler:
            await crawler.run([base + "/report"])
            doc = crawler.store.documents(crawler.run_id)[0]
    assert doc["text"].startswith("Observatory public forecast\n")
    assert doc["text"].count("Observatory public forecast") == 1
    assert doc["metadata"]["verification"]["accepted"]


async def test_transport_timeout_has_actionable_phase_and_budget(tmp_path):
    release = asyncio.Event()

    async def handler(request):
        if request.path == "/robots.txt":
            return web.Response(text="User-agent: *\nAllow: /\n")
        await release.wait()
        return web.Response(text=ARTICLE)

    async with serve(handler) as base:
        try:
            cfg = replace(config(tmp_path / "timeout.db"), timeout=0.1, retries=0)
            async with Crawler(cfg) as crawler:
                await crawler.run([base + "/slow"])
                doc = crawler.store.documents(crawler.run_id)[0]
        finally:
            release.set()
    assert doc["outcome"] == "error"
    assert "response headers" in doc["reason"] and "0.1s" in doc["reason"]
    assert doc["metadata"]["transport_error"]["kind"] == "timeout"
