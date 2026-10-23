import json

import pytest
from aiohttp import web

from benchmarks.fixture import ARTICLE, SECOND
from optimal_crawler.browser import Browser
from optimal_crawler.crawler import Crawler
from optimal_crawler.extract import Extractor
from optimal_crawler.models import (
    Candidate,
    CrawlConfig,
    Extraction,
    FetchResult,
    Outcome,
    Strategy,
)
from tests.test_realworld_regressions import serve

THIRD = "The mountain laboratory releases a public catalogue of calibrated instruments. " \
    "Each instrument includes a repair history, measurement uncertainty and an independent review."
SECTIONS = f"<h1>Laboratory report</h1><h2>Observatory findings</h2><p>{ARTICLE}</p>" \
    f"<h2>Archive findings</h2><p>{SECOND}</p><h2>Instrument catalogue</h2><p>{THIRD}</p>"
COMPLETE = f"Laboratory report\nObservatory findings\n{ARTICLE}\nArchive findings\n{SECOND}\n" \
    f"Instrument catalogue\n{THIRD}"
DESCRIPTION = "The laboratory publishes descriptions of its research facilities and public collections. " \
    "Visitors can explore the observatory, historical archive and instrument catalogue through this page."


def shell():
    metadata = json.dumps({"@type": "WebPage", "description": DESCRIPTION})
    return f"<script type='application/ld+json'>{metadata}</script><main id='app'>Loading...</main>" \
        "<script>setTimeout(()=>document.querySelector('main').innerHTML=" + json.dumps(SECTIONS) + ",650)</script>"


async def test_metadata_fragment_does_not_suppress_browser_recovery(tmp_path):
    async def handler(request):
        if request.path == "/robots.txt":
            return web.Response(text="User-agent: *\nAllow: /\n")
        return web.Response(text=shell(), content_type="text/html")

    async with serve(handler) as base:
        config = CrawlConfig(db=tmp_path / "shell.db", allow_private=True, min_delay=0,
                             browser_timeout=4, render_wait=0.1, discover=False, follow_links=False)
        async with Crawler(config) as crawler:
            await crawler.run([base + "/report"])
            document = crawler.store.documents(crawler.run_id)[0]
    assert document["outcome"] == "ok"
    assert document["method"] == "browser"
    assert all(part in document["text"] for part in ("rare helium signal", "thirty six maps", "repair history"))


@pytest.mark.parametrize("strategy", [Strategy.HTTP, Strategy.ADAPTIVE])
async def test_complete_candidate_wins_over_high_scored_fragment(tmp_path, monkeypatch, strategy):
    async def handler(request):
        if request.path == "/robots.txt":
            return web.Response(text="User-agent: *\nAllow: /\n")
        return web.Response(text="<main>" + SECTIONS + "</main>", content_type="text/html")

    def candidates(self, result, structured=True):
        return Extraction(candidates=[Candidate("empty", "No article.", "Laboratory report", 0.2),
                                      Candidate("fragment", ARTICLE, "Laboratory report", 0.98),
                                      Candidate("full", COMPLETE, "Laboratory report", 0.86)])

    monkeypatch.setattr(Extractor, "extract", candidates)
    async with serve(handler) as base:
        config = CrawlConfig(db=tmp_path / "candidates.db", strategy=strategy,
                             allow_private=True, min_delay=0, discover=False, follow_links=False)
        async with Crawler(config) as crawler:
            await crawler.run([base + "/report"])
            document = crawler.store.documents(crawler.run_id)[0]
    assert document["outcome"] == "ok"
    assert document["parser"] == "full"
    assert "repair history" in document["text"]


@pytest.mark.parametrize("strategy", [Strategy.HTTP, Strategy.BROWSER, Strategy.ADAPTIVE])
async def test_redirect_losing_requested_item_is_not_reported_as_success(tmp_path, strategy):
    requests = []

    async def handler(request):
        requests.append(request.path)
        if request.path == "/robots.txt":
            return web.Response(text="User-agent: *\nAllow: /\n")
        if request.path.startswith("/products/"):
            raise web.HTTPFound("/catalogue")
        return web.Response(text="<main>" + SECTIONS + "</main>", content_type="text/html")

    async with serve(handler) as base:
        config = CrawlConfig(db=tmp_path / "identity.db", allow_private=True, min_delay=0,
                             discover=False, follow_links=False, strategy=strategy)
        async with Crawler(config) as crawler:
            summary = await crawler.run([base + "/products/instrument-123456"])
            document = crawler.store.documents(crawler.run_id)[0]
    assert document["outcome"] == "incomplete"
    assert "identifier" in document["reason"].lower()
    assert summary["browser_pages"] == 0
    assert requests.count("/products/instrument-123456") == requests.count("/catalogue") == 1


async def test_learned_browser_is_not_attempted_twice_for_one_page(tmp_path, monkeypatch):
    attempts = []

    async def browser_failure(self, url):
        attempts.append(url)
        return FetchResult(url, outcome=Outcome.ERROR, reason="Controlled rendering failure")

    monkeypatch.setattr(Browser, "fetch", browser_failure)

    async def handler(request):
        if request.path == "/robots.txt":
            return web.Response(text="User-agent: *\nAllow: /\n")
        return web.Response(text=shell(), content_type="text/html")

    async with serve(handler) as base:
        config = CrawlConfig(db=tmp_path / "bounded.db", allow_private=True, min_delay=0,
                             discover=False, follow_links=False)
        async with Crawler(config) as crawler:
            monkeypatch.setattr(crawler.store, "learned_browser", lambda url: True)
            await crawler.run([base + "/report"])
    assert len(attempts) == 1


async def test_legacy_cached_representation_is_fetched_and_verified_again(tmp_path):
    conditional = []

    async def handler(request):
        if request.path == "/robots.txt":
            return web.Response(text="User-agent: *\nAllow: /\n")
        conditional.append(request.headers.get("If-None-Match"))
        if conditional[-1]:
            return web.Response(status=304)
        return web.Response(text="<main>" + SECTIONS + "</main>", content_type="text/html",
                            headers={"ETag": '"report-v1"'})

    async with serve(handler) as base:
        config = CrawlConfig(db=tmp_path / "legacy.db", allow_private=True, min_delay=0,
                             discover=False, follow_links=False)
        async with Crawler(config) as crawler:
            await crawler.run([base + "/report"])
            document = crawler.store.documents(crawler.run_id)[0]
            document["text"] = DESCRIPTION
            document["metadata"].pop("verification")
            crawler.store.db.execute("UPDATE pages SET document=? WHERE url=?",
                                     (json.dumps(document), base + "/report"))
            crawler.store.db.commit()
        async with Crawler(config) as crawler:
            await crawler.run([base + "/report"])
            document = crawler.store.documents(crawler.run_id)[0]
    assert conditional == [None, None]
    assert document["outcome"] == "ok"
    assert "repair history" in document["text"]


async def test_api_recovery_must_cover_original_page_sections(tmp_path, monkeypatch):
    original_extract = Extractor.extract

    def extract(self, result, structured=True):
        if result.url.endswith("/report"):
            return Extraction(candidates=[Candidate("fragment", ARTICLE, "Laboratory report", 0.98)],
                              alternatives=[result.url.replace("/report", "/api")])
        return original_extract(self, result, structured)

    monkeypatch.setattr(Extractor, "extract", extract)

    async def handler(request):
        if request.path == "/robots.txt":
            return web.Response(text="User-agent: *\nAllow: /\n")
        if request.path == "/api":
            return web.json_response({"title": "Laboratory report", "articleBody": _api_text})
        return web.Response(text="<main>" + SECTIONS + "</main>", content_type="text/html")

    for name, _api_text, expected in [("fragment", ARTICLE, "incomplete"), ("full", COMPLETE, "ok")]:
        async with serve(handler) as base:
            config = CrawlConfig(db=tmp_path / f"{name}.db", allow_private=True, min_delay=0,
                                 browser_timeout=2, render_wait=0, discover=False, follow_links=False)
            async with Crawler(config) as crawler:
                summary = await crawler.run([base + "/report"])
                document = crawler.store.documents(crawler.run_id)[0]
                api_attempt = next(a for a in crawler.store.events("attempts", crawler.run_id)
                                   if a["method"] == "api")
        assert api_attempt["outcome"] == expected
        assert document["outcome"] == expected
        if expected == "ok":
            assert document["method"] == "api"
            assert "repair history" in document["text"]
            assert summary["browser_pages"] == 0
        else:
            assert document["text"] == ""
            assert summary["browser_pages"] == 1
