import asyncio
import json
from collections import Counter

import pytest
from aiohttp import web

from benchmarks.fixture import ARTICLE
from optimal_crawler.crawler import Crawler
from optimal_crawler.models import CrawlConfig, Outcome
from optimal_crawler.network import Network
from optimal_crawler.store import Store
from tests.test_realworld_regressions import serve


async def test_page_budget_is_independent_of_single_request_timeout(tmp_path):
    receipts = []

    async def handler(request):
        receipts.append(request.path)
        if request.path == "/robots.txt":
            return web.Response(text="User-agent: *\nAllow: /\n")
        if request.path == "/article":
            return web.Response(text="<main id='content'>Loading...</main><script>"
                                "import('/step/1.js');</script>", content_type="text/html")
        step = int(request.path.split("/")[-1].split(".")[0])
        await asyncio.sleep(0.12)
        script = f"import('/step/{step + 1}.js');" if step < 5 else \
            "document.getElementById('content').textContent=" + json.dumps(ARTICLE)
        return web.Response(text=script, content_type="application/javascript")

    async with serve(handler) as base:
        config = CrawlConfig(db=tmp_path / "chain.db", allow_private=True, min_delay=0.12,
                             timeout=0.4, browser_timeout=5, render_wait=0.1,
                             discover=False, follow_links=False)
        async with Crawler(config) as crawler:
            await crawler.run([base + "/article"])
            document = crawler.store.documents(crawler.run_id)[0]
    assert document["outcome"] == "ok"
    assert "rare helium signal" in document["text"]
    assert receipts.count("/step/5.js") == 1
    assert document["elapsed_ms"] > config.timeout * 1000


async def test_stylesheet_load_event_can_unblock_application(tmp_path):
    receipts = Counter()

    async def handler(request):
        receipts[request.path] += 1
        if request.path == "/robots.txt":
            return web.Response(text="User-agent: *\nAllow: /\n")
        if request.path == "/app.css":
            return web.Response(text="main { color: black; }", content_type="text/css")
        return web.Response(text="<main id='content'>Loading...</main><script>"
                            "const link=document.createElement('link');link.rel='stylesheet';"
                            "link.href='/app.css';link.onload=()=>{"
                            "document.getElementById('content').textContent=" + json.dumps(ARTICLE) +
                            "};document.head.append(link);</script>", content_type="text/html")

    async with serve(handler) as base:
        config = CrawlConfig(db=tmp_path / "css.db", allow_private=True, min_delay=0,
                             browser_timeout=3, discover=False, follow_links=False)
        async with Crawler(config) as crawler:
            await crawler.run([base + "/article"])
            document = crawler.store.documents(crawler.run_id)[0]
    assert document["outcome"] == "ok"
    assert "rare helium signal" in document["text"]
    assert receipts["/app.css"] == 1


async def test_authorized_robots_redirect_applies_rules_to_original_origin(tmp_path):
    first_receipts, policy_receipts = Counter(), Counter()

    async def policy(request):
        policy_receipts[request.path] += 1
        return web.Response(text="User-agent: *\nDisallow: /private\nAllow: /\n")

    async with serve(policy) as policy_base:
        async def first(request):
            first_receipts[request.path] += 1
            if request.path == "/robots.txt":
                raise web.HTTPFound(policy_base + "/shared-policy.txt")
            return web.Response(text=ARTICLE)

        async with serve(first) as base:
            config = CrawlConfig(db=tmp_path / "robots.db", allow_private=True, min_delay=0,
                                 asset_origins=(policy_base,))
            store = Store(config.db)
            run = store.start(config, [base])
            try:
                async with Network(config, store, run, {base}) as network:
                    assert (await network.fetch(base + "/public")).outcome == Outcome.OK
                    assert (await network.fetch(base + "/private")).outcome == Outcome.ROBOTS
                    assert (await network.fetch(policy_base + "/article")).outcome == Outcome.OUT_OF_SCOPE
            finally:
                store.close()
    assert first_receipts["/private"] == 0
    assert first_receipts["/public"] == policy_receipts["/shared-policy.txt"] == 1
    assert policy_receipts["/article"] == 0


@pytest.mark.parametrize("excluded", [False, True])
async def test_robots_redirect_cannot_expand_scope_or_override_exclusion(tmp_path, excluded):
    receipts = []

    async def second(request):
        receipts.append(request.path)
        return web.Response(text="User-agent: *\nAllow: /\n")

    async with serve(second) as other:
        async def first(request):
            raise web.HTTPFound(other + "/robots.txt")

        async with serve(first) as base:
            config = CrawlConfig(db=tmp_path / "blocked.db", allow_private=True, min_delay=0,
                                 asset_origins=(other,) if excluded else (),
                                 excluded_origins=(other,) if excluded else ())
            store = Store(config.db)
            run = store.start(config, [base])
            try:
                async with Network(config, store, run, {base}) as network:
                    result = await network.fetch(base + "/article")
                    assert result.outcome == Outcome.ROBOTS_UNAVAILABLE
            finally:
                store.close()
    assert receipts == []


async def test_browser_reports_dependency_refusal_without_fetching_it(tmp_path):
    receipts = Counter()

    async def handler(request):
        receipts[request.path] += 1
        if request.path == "/robots.txt":
            return web.Response(text="User-agent: *\nDisallow: /private\n")
        return web.Response(text="<main>Loading...</main><script>"
                            "fetch('/private/data').catch(()=>{});</script>", content_type="text/html")

    async with serve(handler) as base:
        config = CrawlConfig(db=tmp_path / "diagnostic.db", allow_private=True, min_delay=0,
                             browser_timeout=1.5, discover=False, follow_links=False)
        async with Crawler(config) as crawler:
            await crawler.run([base + "/article"])
            document = crawler.store.documents(crawler.run_id)[0]
    assert document["outcome"] == "low_quality"
    assert document["metadata"]["browser"]["dependency_refusals"][0]["outcome"] == "robots_denied"
    assert document["metadata"]["browser"]["deadline_reached"]
    assert receipts["/private/data"] == 0


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_browser_budget_requires_a_positive_finite_value(value):
    with pytest.raises(ValueError):
        CrawlConfig(browser_timeout=value)


async def test_cross_origin_api_receives_origin_but_no_cookie(tmp_path):
    receipts = []

    async def api(request):
        if request.path == "/robots.txt":
            return web.Response(text="User-agent: *\nAllow: /\n")
        receipts.append(dict(request.headers))
        headers = {"Access-Control-Allow-Credentials": "true"}
        if request.headers.get("Origin"):
            headers["Access-Control-Allow-Origin"] = request.headers["Origin"]
        return web.json_response({"text": ARTICLE}, headers=headers)

    async with serve(api) as api_base:
        async def page(request):
            if request.path == "/robots.txt":
                return web.Response(text="User-agent: *\nAllow: /\n")
            return web.Response(text="<main id='content'>Loading...</main><script>"
                                "document.cookie='private_session=do-not-forward';fetch(" +
                                json.dumps(api_base + "/data") + ",{credentials:'include'})"
                                ".then(r=>r.json()).then(d=>document.getElementById('content').textContent=d.text);"
                                "</script>", content_type="text/html")

        async with serve(page) as base:
            config = CrawlConfig(db=tmp_path / "cors.db", allow_private=True, min_delay=0,
                                 browser_timeout=3, asset_origins=(api_base,),
                                 discover=False, follow_links=False)
            async with Crawler(config) as crawler:
                await crawler.run([base + "/article"])
                document = crawler.store.documents(crawler.run_id)[0]
    assert document["outcome"] == "ok"
    assert "rare helium signal" in document["text"]
    assert receipts[0]["Origin"] == base
    assert "Cookie" not in receipts[0]
    assert "Authorization" not in receipts[0]


async def test_head_is_preserved_and_does_not_download_response_body(tmp_path):
    receipts = []

    async def handler(request):
        receipts.append((request.method, request.path))
        if request.path == "/robots.txt":
            return web.Response(text="User-agent: *\nAllow: /\n")
        return web.Response(headers={"Content-Length": "1000000"})

    async with serve(handler, method="*") as base:
        config = CrawlConfig(db=tmp_path / "head.db", allow_private=True, min_delay=0, max_bytes=500)
        store = Store(config.db)
        run = store.start(config, [base])
        try:
            async with Network(config, store, run, {base}) as network:
                result = await network.fetch(base + "/health", method="HEAD")
                assert result.outcome == Outcome.OK
                assert result.body == b""
                assert (await network.fetch(base + "/mutate", method="POST")).outcome == Outcome.UNSUPPORTED
        finally:
            store.close()
    assert receipts == [("GET", "/robots.txt"), ("HEAD", "/health")]


async def test_favicon_head_connectivity_probe_reaches_guarded_transport(tmp_path):
    receipts = []

    async def handler(request):
        receipts.append((request.method, request.path))
        if request.path == "/robots.txt":
            return web.Response(text="User-agent: *\nAllow: /\n")
        if request.path == "/favicon.ico":
            return web.Response(status=204)
        return web.Response(text="<main id='content'>Loading...</main><script>"
                            "fetch('/favicon.ico',{method:'HEAD'}).then(r=>{if(r.ok)"
                            "document.getElementById('content').textContent=" + json.dumps(ARTICLE) +
                            "});</script>", content_type="text/html")

    async with serve(handler, method="*") as base:
        config = CrawlConfig(db=tmp_path / "favicon.db", allow_private=True, min_delay=0,
                             browser_timeout=3, discover=False, follow_links=False)
        async with Crawler(config) as crawler:
            await crawler.run([base + "/article"])
            document = crawler.store.documents(crawler.run_id)[0]
            requests = crawler.store.events("requests", crawler.run_id)
    assert document["outcome"] == "ok"
    assert "rare helium signal" in document["text"]
    assert ("HEAD", "/favicon.ico") in receipts
    assert any(r["url"].endswith("/favicon.ico") and r["method"] == "HEAD" for r in requests)
    assert len(requests) == len(receipts)


async def test_uninstrumented_browser_target_cannot_escape_local_deny_proxy(tmp_path):
    escaped = []

    async def target(request):
        escaped.append(request.path)
        return web.Response(text=ARTICLE)

    async def page(request):
        return web.Response(text="User-agent: *\nAllow: /\n" if request.path == "/robots.txt"
                            else f"<main>{ARTICLE}</main>", content_type="text/html")

    async with serve(target) as forbidden, serve(page) as base:
        config = CrawlConfig(db=tmp_path / "proxy.db", allow_private=True, min_delay=0,
                             discover=False, follow_links=False)
        async with Crawler(config) as crawler:
            await crawler.run([base + "/article"])
            await crawler.browser._start()
            context = await crawler.browser._browser.new_context()
            browser_page = await context.new_page()
            response = await browser_page.goto(forbidden + "/secret")
            assert response.status == 403
            await context.close()
    assert escaped == []


async def test_render_settle_window_observes_a_delayed_content_update(tmp_path):
    ending = "The final measurements include thirty six maps and violet comets."

    async def handler(request):
        if request.path == "/robots.txt":
            return web.Response(text="User-agent: *\nAllow: /\n")
        return web.Response(text="<main id='content'>Loading...</main><script>"
                            "setTimeout(()=>document.getElementById('content').textContent=" +
                            json.dumps(ARTICLE) + ",10);"
                            "setTimeout(()=>document.getElementById('content').textContent+=" +
                            json.dumps(ending) + ",500);</script>", content_type="text/html")

    async with serve(handler) as base:
        config = CrawlConfig(db=tmp_path / "settle.db", allow_private=True, min_delay=0,
                             browser_timeout=3, render_wait=0.75, discover=False, follow_links=False)
        async with Crawler(config) as crawler:
            await crawler.run([base + "/article"])
            document = crawler.store.documents(crawler.run_id)[0]
    assert ending in document["text"]


async def test_browser_verification_wall_is_refused_before_script_execution(tmp_path):
    receipts = Counter()

    async def handler(request):
        receipts[request.path] += 1
        if request.path == "/robots.txt":
            return web.Response(text="User-agent: *\nAllow: /\n")
        return web.Response(text="<title>Verifying your browser</title>"
                            "<p>Verifying your browser, please wait</p>"
                            "<script>fetch('/verification-work');</script>", content_type="text/html")

    async with serve(handler) as base:
        config = CrawlConfig(db=tmp_path / "verification.db", strategy="browser",
                             allow_private=True, min_delay=0, discover=False, follow_links=False)
        async with Crawler(config) as crawler:
            summary = await crawler.run([base + "/wall"])
    assert summary["outcomes"] == {"access_wall": 1}
    assert summary["browser_pages"] == 0
    assert receipts["/verification-work"] == 0
