import asyncio
import json
from contextlib import AsyncExitStack

from aiohttp import web

from benchmarks.fixture import ARTICLE
from optimal_crawler.crawler import Crawler
from optimal_crawler.extract import Extractor
from optimal_crawler.models import CrawlConfig, FetchResult, Strategy
from tests.test_realworld_regressions import serve


async def test_waiting_for_browser_slot_does_not_consume_page_budget(tmp_path):
    async def handler(request):
        if request.path == "/robots.txt":
            return web.Response(text="User-agent: *\nAllow: /\n")
        return web.Response(text="<main id='content'>Loading...</main><script>"
                            "setTimeout(()=>document.getElementById('content').textContent=" +
                            json.dumps(ARTICLE) + ",1500)</script>", content_type="text/html")

    async with AsyncExitStack() as stack:
        bases = [await stack.enter_async_context(serve(handler)) for _ in range(3)]
        config = CrawlConfig(db=tmp_path / "queue.db", allow_private=True, min_delay=0,
                             workers=3, browser_concurrency=1, browser_timeout=2.5,
                             render_wait=0.1, discover=False, follow_links=False)
        async with Crawler(config) as crawler:
            await crawler.run([base + "/article" for base in bases])
            documents = crawler.store.documents(crawler.run_id)
    assert len(documents) == 3
    assert all(d["outcome"] == "ok" and "rare helium signal" in d["text"] for d in documents)


async def test_structured_article_with_short_visible_title_does_not_wait_for_deadline(tmp_path):
    async def handler(request):
        if request.path == "/robots.txt":
            return web.Response(text="User-agent: *\nAllow: /\n")
        body = "<h1>Report</h1><script type='application/ld+json'>" + json.dumps(
            {"@type": "Article", "articleBody": ARTICLE}) + "</script>"
        return web.Response(text=body, content_type="text/html")

    async with serve(handler) as base:
        config = CrawlConfig(db=tmp_path / "structured.db", strategy=Strategy.BROWSER,
                             allow_private=True, min_delay=0, browser_timeout=10,
                             discover=False, follow_links=False)
        async with Crawler(config) as crawler:
            await asyncio.wait_for(crawler.run([base + "/article"]), timeout=3)
            document = crawler.store.documents(crawler.run_id)[0]
    assert document["outcome"] == "ok"
    assert "rare helium signal" in document["text"]


def test_sibling_article_sections_are_not_mistaken_for_the_complete_page():
    sections = ["Starter plan costs $5 per month. " * 10,
                "Developer plan costs $20 per month. " * 10,
                "Enterprise plan has custom pricing. " * 10]
    page = "<html><body><nav>BOILERPLATE_SENTINEL</nav><h1>Product pricing</h1>" + "".join(
        "<article><h2>" + title + "</h2><p>" + content + "</p></article>"
        for title, content in zip(("Starter", "Developer", "Enterprise"), sections, strict=True)) + "</body></html>"
    result = Extractor().extract(FetchResult("https://example.test/pricing", 200, page.encode(),
                                             {"content-type": "text/html"}))
    assert all(marker in result.best.text for marker in ("Product pricing", "$5", "$20", "custom pricing"))
    assert "BOILERPLATE_SENTINEL" not in result.best.text


def test_existing_main_is_preferred_to_unrelated_body_content():
    page = "<main><h1>Science report</h1><article>" + ARTICLE + "</article></main>" + \
        "<div>UNRELATED_BODY_SENTINEL</div>"
    result = Extractor().extract(FetchResult("https://example.test/article", 200, page.encode(),
                                             {"content-type": "text/html"}))
    assert "rare helium signal" in result.best.text
    assert "UNRELATED_BODY_SENTINEL" not in result.best.text
