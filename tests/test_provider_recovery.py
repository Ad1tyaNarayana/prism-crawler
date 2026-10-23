import pytest
from aiohttp import web

from benchmarks.fixture import ARTICLE
from optimal_crawler.crawler import Crawler
from optimal_crawler.extract import Extractor
from optimal_crawler.models import CrawlConfig, FetchResult
from tests.test_realworld_regressions import serve


@pytest.mark.parametrize(
    "page,expected",
    [
        ("<html><head><title>World News | Example</title></head>"
         f"<body><main><h1>World</h1><p>{ARTICLE}</p></main></body></html>",
         "World News | Example"),
        ("<main><article><h1>First report</h1>"
         f"<p>{ARTICLE}</p></article><article><h1>Second report</h1>"
         f"<p>{ARTICLE}</p></article></main>", "First report"),
    ],
)
def test_html_title_uses_document_title_or_first_heading(page, expected):
    result = FetchResult("https://example.com/report", 200, page.encode(),
                         {"content-type": "text/html"})
    extraction = Extractor().extract(result)
    assert extraction.best.title == expected


async def test_export_preserves_document_title_and_main_heading(tmp_path):
    async def handler(request):
        if request.path == "/robots.txt":
            return web.Response(text="User-agent: *\nAllow: /\n")
        return web.Response(
            text="<html><head><title>World News | Example</title></head><body>"
                 f"<main><h1>World</h1><p>{ARTICLE}</p></main></body></html>",
            content_type="text/html",
        )

    async with serve(handler) as base:
        config = CrawlConfig(db=tmp_path / "title.db", allow_private=True, min_delay=0,
                             discover=False, follow_links=False)
        async with Crawler(config) as crawler:
            summary = await crawler.run([base + "/report"])
            document = crawler.store.documents(crawler.run_id)[0]
    assert summary["outcomes"] == {"ok": 1}
    assert summary["browser_pages"] == 0
    assert document["title"] == "World News | Example"
    assert "World News | Example\nWorld\n" in document["text"]
    assert ARTICLE in document["text"]
