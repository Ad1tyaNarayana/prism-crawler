import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from aiohttp import web

from benchmarks.fixture import ARTICLE, SECOND
from optimal_crawler.crawler import Crawler
from optimal_crawler.extract import Extractor
from optimal_crawler.models import CrawlConfig, FetchResult


@asynccontextmanager
async def serve(handler, method="GET"):
    app = web.Application()
    app.router.add_route(method, "/{path:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    server = web.TCPSite(runner, "127.0.0.1", 0)
    await server.start()
    port = server._server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        await runner.cleanup()


def extract(markup):
    return Extractor().extract(FetchResult("https://example.org/article", body=markup.encode(),
                                         headers={"content-type": "text/html"}))


async def test_large_valid_headers_do_not_make_robots_unavailable(tmp_path):
    async def handler(request):
        text = "User-agent: *\nAllow: /\n" if request.path == "/robots.txt" else f"<main>{ARTICLE}</main>"
        return web.Response(text=text, content_type="text/html",
                            headers={"Content-Security-Policy": "default-src 'self'; " + "https://cdn.example.org " * 500})

    async with serve(handler) as base:
        config = CrawlConfig(db=tmp_path / "headers.db", allow_private=True, min_delay=0,
                             discover=False, follow_links=False)
        async with Crawler(config) as crawler:
            summary = await crawler.run([base + "/article"])
            document = crawler.store.documents(crawler.run_id)[0]
            assert summary["outcomes"] == {"ok": 1}
            assert "rare helium signal" in document["text"]


def test_structured_answer_does_not_replace_the_complete_article():
    fragment = ARTICLE + " All recorded spectra remain in the public archive."
    structured = json.dumps({"@type": "FAQPage", "mainEntity": [
        {"@type": "Question", "name": "What did the observatory record?",
         "acceptedAnswer": {"@type": "Answer", "text": fragment}}]})
    result = extract(f'<script type="application/ld+json">{structured}</script>'
                     f"<main><h1>Scientific records</h1><p>{fragment}</p><p>{SECOND}</p></main>")
    assert "thirty six maps" in result.best.text
    assert "violet comets" in result.best.text


def test_serialized_component_tree_is_not_saved_as_prose():
    component = json.dumps([["$r", "Paragraph", None, {"children": ARTICLE}],
                            ["$r", "Paragraph", None, {"children": SECOND}]])
    state = json.dumps({"page": {"content": component}})
    result = extract(f'<script type="application/json">{state}</script>'
                     f"<main><p>{ARTICLE}</p><p>{SECOND}</p></main>")
    assert result.best.text.startswith("The observatory")
    assert '"children"' not in result.best.text


@pytest.mark.parametrize("markup", [
    "<title>Client Challenge</title><main>A required part of this site could not load. "
    "Check your network connection and browser settings before trying again.</main>",
    "<title>Hang Tight! Routing to checkout...</title><main>We have our hands full at the moment. "
    "This page will automatically refresh and bring you into the website as soon as we can handle it.</main>",
    "<title></title><script src='https://example.token.awswaf.com/challenge.js'></script>"
    "<noscript>In order to continue, we need to verify that you're not a robot.</noscript>",
])
def test_visible_challenge_pages_are_not_content(markup):
    assert extract(markup).wall


def test_equation_image_alternative_survives_text_extraction():
    result = extract('<main><h1>Right triangles</h1><p>The lengths satisfy '
                     '<img class="numberedequation" alt="a^2+b^2=c^2" src="/formula.svg">'
                     '. The longest side is the hypotenuse opposite the right angle.</p></main>')
    assert "a^2+b^2=c^2" in result.best.text


def test_application_offline_message_is_not_saved_as_content():
    result = extract("<title>Weather map</title><body><div>Open map contributors</div>"
                     "<p>WOW it appears that you are offline :-(</p><p>Download App</p></body>")
    assert result.best is None or result.best.quality < 0.5


async def test_browser_waits_for_a_dependency_chain_beyond_the_settle_window(tmp_path):
    async def handler(request):
        if request.path == "/robots.txt":
            return web.Response(text="User-agent: *\nAllow: /\n")
        if request.path == "/article":
            return web.Response(text="<main id='content'>Loading...</main><script>"
                                "import('/step/1.js');</script>", content_type="text/html")
        step = int(request.path.split("/")[-1].split(".")[0])
        await asyncio.sleep(0.18)
        script = f"import('/step/{step + 1}.js');" if step < 4 else \
            "document.getElementById('content').textContent=" + json.dumps(ARTICLE)
        return web.Response(text=script, content_type="application/javascript")

    async with serve(handler) as base:
        config = CrawlConfig(db=tmp_path / "chain.db", allow_private=True, min_delay=0,
                             discover=False, follow_links=False, render_wait=0.25, timeout=3)
        async with Crawler(config) as crawler:
            await crawler.run([base + "/article"])
            document = crawler.store.documents(crawler.run_id)[0]
            assert document["outcome"] == "ok"
            assert "rare helium signal" in document["text"]


async def test_browser_extracts_open_shadow_root_content(tmp_path):
    async def handler(request):
        if request.path == "/robots.txt":
            return web.Response(text="User-agent: *\nAllow: /\n")
        script = "document.querySelector('article-view').attachShadow({mode:'open'}).innerHTML=" + \
            json.dumps(f"<article><h1>Observatory report</h1><p>{ARTICLE}</p></article>")
        return web.Response(text=f"<article-view></article-view><script>{script}</script>",
                            content_type="text/html")

    async with serve(handler) as base:
        config = CrawlConfig(db=tmp_path / "shadow.db", allow_private=True, min_delay=0,
                             discover=False, follow_links=False, render_wait=0.3, timeout=2)
        async with Crawler(config) as crawler:
            await crawler.run([base + "/article"])
            document = crawler.store.documents(crawler.run_id)[0]
            assert document["method"] == "browser"
            assert document["outcome"] == "ok"
            assert "rare helium signal" in document["text"]
