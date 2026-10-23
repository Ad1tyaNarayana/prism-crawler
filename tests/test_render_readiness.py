import asyncio
import json

from aiohttp import web

from benchmarks.fixture import ARTICLE, SECOND
from optimal_crawler.crawler import Crawler
from optimal_crawler.models import CrawlConfig
from tests.test_realworld_regressions import serve


async def test_rendered_content_can_finish_with_an_unrelated_request_pending(tmp_path):
    release = asyncio.Event()

    async def handler(request):
        if request.path == "/robots.txt":
            return web.Response(text="User-agent: *\nAllow: /\n")
        if request.path == "/background":
            await release.wait()
            return web.Response(text="ok")
        return web.Response(text="<main id='app'>Loading...</main><script>"
            "fetch('/background');setTimeout(()=>document.querySelector('main').textContent="
            + json.dumps(ARTICLE) + ",100)</script>", content_type="text/html")

    async with serve(handler) as base:
        try:
            config = CrawlConfig(db=tmp_path / "ready.db", allow_private=True, min_delay=0,
                                 render_wait=0.15, browser_timeout=2,
                                 discover=False, follow_links=False)
            async with Crawler(config) as crawler:
                await crawler.run([base + "/report"])
                document = crawler.store.documents(crawler.run_id)[0]
        finally:
            release.set()
    assert document["outcome"] == "ok"
    assert "rare helium signal" in document["text"]
    assert document["elapsed_ms"] < 1600
    assert document["metadata"]["browser"]["readiness"] == "verified_rendered_content"
    assert any(url.endswith("/background") for url in document["metadata"]["browser"]["pending_requests"])
    assert all("Invalid InterceptionId" not in error
               for error in document["metadata"]["browser"]["page_errors"])


async def test_readiness_waits_for_explicitly_busy_content(tmp_path):
    async def handler(request):
        if request.path == "/robots.txt":
            return web.Response(text="User-agent: *\nAllow: /\n")
        if request.path == "/content":
            await asyncio.sleep(0.6)
            return web.json_response({"text": SECOND})
        return web.Response(text="<main aria-busy='true'><p>" + ARTICLE + "</p></main><script>"
            "fetch('/content').then(r=>r.json()).then(d=>{const m=document.querySelector('main');"
            "m.insertAdjacentHTML('beforeend','<p>'+d.text+'</p>');m.removeAttribute('aria-busy')});"
            "</script>", content_type="text/html")

    async with serve(handler) as base:
        config = CrawlConfig(db=tmp_path / "busy.db", allow_private=True, min_delay=0,
                             render_wait=0.1, browser_timeout=3, discover=False, follow_links=False)
        async with Crawler(config) as crawler:
            await crawler.run([base + "/report"])
            document = crawler.store.documents(crawler.run_id)[0]
    assert document["outcome"] == "ok"
    assert "rare helium signal" in document["text"] and "thirty six maps" in document["text"]
    assert document["elapsed_ms"] >= 600
