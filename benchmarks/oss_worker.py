"""One isolated, local-library trial with explicit adapter and policy receipts."""

import argparse
import asyncio
import importlib.metadata
import json
import time
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from benchmarks.oss_policy import BLOCKED_TYPES, UA, Policy


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str))


class Instrumentation:
    def __init__(self, policy, guarded):
        self.policy, self.guarded = policy, guarded
        self.patches = []
        self.contexts = set()
        self.counted = set()
        self.admitted = set()

    def __enter__(self):
        from playwright.async_api import Browser, BrowserContext, BrowserType, Route

        original_launch = BrowserType.launch
        original_context = Browser.new_context
        original_page = BrowserContext.new_page
        original_continue = Route.continue_
        owner = self

        async def launch(instance, *a, **kw):
            start = time.monotonic()
            owner.policy.launches += 1
            try:
                return await original_launch(instance, *a, **kw)
            finally:
                owner.policy.launch_ms += (time.monotonic() - start) * 1000

        async def new_context(instance, *a, **kw):
            kw.update(
                user_agent=UA,
                service_workers="block",
                ignore_https_errors=False,
                viewport={"width": 1280, "height": 720},
                locale="en-US",
                timezone_id="UTC",
            )
            context = await original_context(instance, *a, **kw)
            if owner.guarded:
                # Every request has a route even when a framework adds its own page handlers.
                await context.route("**/*", lambda route: route.continue_())
                if hasattr(context, "route_web_socket"):
                    await context.route_web_socket("**/*", lambda ws: ws.close())

                async def response_received(response):
                    request = response.request
                    try:
                        if response.headers.get("cf-mitigated") == "challenge":
                            owner.policy.cf = True
                        body = await response.body() if request.is_navigation_request() else b""
                        owner.policy.capture(
                            response.url,
                            response.status,
                            response.headers,
                            body,
                            "browser_document"
                            if request.is_navigation_request()
                            else "browser_asset",
                        )
                    except Exception:
                        pass

                context.on("response", response_received)
            return context

        async def new_page(instance, *a, **kw):
            page = await original_page(instance, *a, **kw)
            if id(page) not in owner.counted:
                owner.policy.pages += 1
                owner.counted.add(id(page))
            return page

        async def continued(route, *a, **kw):
            if owner.guarded:
                request = route.request
                if request.resource_type in BLOCKED_TYPES:
                    await route.abort()
                    return
                identity = id(request._impl_obj)
                if identity not in owner.admitted:
                    reason = await owner.policy.allow(
                        request.url,
                        request.method,
                        "document" if request.is_navigation_request() else "asset",
                    )
                    if reason:
                        await route.abort()
                        return
                    owner.admitted.add(identity)
                headers = {
                    k: v
                    for k, v in (kw.get("headers") or request.headers).items()
                    if k.lower() not in {"cookie", "authorization"}
                }
                headers["user-agent"] = UA
                kw["headers"] = headers
            return await original_continue(route, *a, **kw)

        self.patches = [
            patch.object(BrowserType, "launch", launch),
            patch.object(Browser, "new_context", new_context),
            patch.object(BrowserContext, "new_page", new_page),
            patch.object(Route, "continue_", continued),
        ]
        if self.guarded:
            import importlib

            for module_name in ("httpx", "httpx2"):
                try:
                    module = importlib.import_module(module_name)
                except ImportError:
                    continue
                original = module.AsyncClient._send_single_request

                async def http_send(instance, request, original=original, module=module):
                    reason = await owner.policy.allow(str(request.url), request.method, "document")
                    if reason:
                        return module.Response(
                            403, request=request, content=("Benchmark policy: " + reason).encode()
                        )
                    request.headers["user-agent"] = UA
                    request.headers.pop("cookie", None)
                    request.headers.pop("authorization", None)
                    response = await original(instance, request)
                    body = await response.aread()
                    owner.policy.capture(
                        str(request.url), response.status_code, response.headers, body, "http"
                    )
                    return response

                self.patches.append(
                    patch.object(module.AsyncClient, "_send_single_request", http_send)
                )
        for item in self.patches:
            item.start()
        return self

    def __exit__(self, *_):
        for item in reversed(self.patches):
            item.stop()


async def prism(case, folder, policy):
    from optimal_crawler.crawler import Crawler
    from optimal_crawler.models import CrawlConfig
    from optimal_crawler.network import Network

    config = CrawlConfig(
        db=folder / "crawl.db",
        max_pages=1,
        max_requests=140,
        workers=1,
        browser_concurrency=1,
        min_delay=0.5,
        timeout=12,
        browser_timeout=35,
        run_timeout=45,
        retries=0,
        discover=False,
        follow_links=False,
        render_wait=1,
        user_agent=UA,
        allow_private=policy.private,
        asset_origins=tuple(case.get("asset_origins", [])),
        redirect_origins=tuple(case.get("redirect_origins", [])),
    )
    original = Network._raw

    async def observed(instance, *a, **kw):
        result = await original(instance, *a, **kw)
        policy.capture(
            result.url,
            result.status,
            result.headers,
            result.body,
            a[2] if len(a) > 2 else kw.get("role", "unknown"),
        )
        return result

    with patch.object(Network, "_raw", observed):
        async with Crawler(config) as crawler:
            summary = await crawler.run([case["url"]])
            docs = crawler.store.documents(crawler.run_id)
            policy.count = len(crawler.store.events("requests", crawler.run_id))
    return {
        **(docs[0] if docs else {"text": "", "outcome": "error", "reason": "No saved document"}),
        "summary": summary,
    }


def generic_extract(raw, url):
    import trafilatura

    text = trafilatura.extract(
        raw,
        url=url,
        include_comments=False,
        include_tables=True,
        include_links=False,
        favor_recall=True,
        fast=True,
    )
    if text:
        return text
    from bs4 import BeautifulSoup

    tree = BeautifulSoup(raw, "lxml")
    for element in tree.select(
        'script,style,noscript,nav,footer,aside,form,[hidden],[aria-hidden="true"]'
    ):
        element.decompose()
    root = tree.select_one('main,[role="main"],article') or tree
    return root.get_text("\n", strip=True)


async def crawlee(case, folder, policy):
    from crawlee import ConcurrencySettings, Request
    from crawlee.configuration import Configuration
    from crawlee.crawlers import AdaptivePlaywrightCrawler
    from crawlee.http_clients import HttpxHttpClient

    captured = []

    def adequate(result):
        for call in result.push_data_calls:
            data = call["data"]
            if (
                isinstance(data, dict)
                and len(data.get("text", "")) >= 100
                and data["text"].strip().casefold() not in {"loading", "loading..."}
            ):
                return True
        return False

    crawler = AdaptivePlaywrightCrawler.with_beautifulsoup_static_parser(
        max_requests_per_crawl=1,
        max_request_retries=0,
        max_session_rotations=0,
        use_session_pool=False,
        retry_on_blocked=False,
        respect_robots_txt_file=False,
        concurrency_settings=ConcurrencySettings(
            min_concurrency=1, max_concurrency=1, desired_concurrency=1
        ),
        configuration=Configuration(storage_dir=str(folder / "storage"), log_level="ERROR"),
        http_client=HttpxHttpClient(
            header_generator=None, persist_cookies_per_session=False, timeout=12
        ),
        request_handler_timeout=timedelta(seconds=40),
        result_checker=adequate,
        playwright_crawler_specific_kwargs={
            "fingerprint_generator": None,
            "headless": True,
            "browser_type": "chromium",
            "use_incognito_pages": True,
            "navigation_timeout": timedelta(seconds=30),
            "goto_options": {"wait_until": "domcontentloaded"},
            "browser_new_context_options": {"user_agent": UA, "service_workers": "block"},
        },
    )

    @crawler.router.default_handler
    async def handler(context):
        if context._page:
            # A bounded settling interval is shared with the other browser adapters.
            await context.page.wait_for_timeout(1000)
        parsed = await context.parse_with_static_parser()
        raw = str(parsed)
        text = generic_extract(raw, context.request.url)
        record = {
            "text": text,
            "status": context.http_response.status_code,
            "method": "browser" if context._page else "http",
            "outcome": "ok" if text else "low_quality",
            "final_url": context.request.loaded_url or context.request.url,
        }
        captured.append(record)
        await context.push_data(record)
        (folder / ("rendered.html" if context._page else "http.html")).write_text(raw)

    await crawler.run([Request.from_url(case["url"], headers={"User-Agent": UA})])
    data = await crawler.get_data()
    records = list(data.items)
    return (
        records[-1]
        if records
        else {
            "text": "",
            "outcome": "error",
            "reason": "No committed Crawlee dataset item",
            "attempted_handler_results": captured,
        }
    )


async def crawl4ai(case, folder, policy):
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
    from crawl4ai.content_filter_strategy import PruningContentFilter
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

    browser = BrowserConfig(
        headless=True,
        user_agent=UA,
        text_mode=True,
        enable_stealth=False,
        ignore_https_errors=False,
        verbose=False,
        java_script_enabled=True,
    )
    config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        check_robots_txt=False,
        simulate_user=False,
        override_navigator=False,
        magic=False,
        wait_until="domcontentloaded",
        page_timeout=30000,
        delay_before_return_html=1,
        scan_full_page=False,
        remove_overlay_elements=False,
        verbose=False,
        markdown_generator=DefaultMarkdownGenerator(
            content_filter=PruningContentFilter(
                threshold=0.48, threshold_type="fixed", min_word_threshold=0
            ),
            options={"ignore_links": True, "ignore_images": True},
        ),
    )
    async with AsyncWebCrawler(
        config=browser, base_directory=str(folder), verbose=False
    ) as crawler:
        result = await crawler.arun(case["url"], config=config)
    markdown = result.markdown
    raw = markdown.raw_markdown if markdown else ""
    fit = markdown.fit_markdown if markdown else ""
    if result.html:
        (folder / "rendered.html").write_text(result.html)
    return {
        "text": fit or raw,
        "raw_markdown": raw,
        "fit_markdown": fit,
        "outcome": "ok" if result.success else "error",
        "reason": result.error_message,
        "status": result.status_code,
        "method": "browser",
        "final_url": result.redirected_url or result.url,
    }


async def run(args):
    case = json.loads(args.case.read_text())
    folder = args.output
    folder.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    result = {"text": "", "outcome": "error", "reason": "Adapter did not finish"}
    async with Policy(case, folder, args.allow_private) as policy:
        try:
            with Instrumentation(policy, args.provider != "prism"):
                async with asyncio.timeout(45):
                    result = await {"prism": prism, "crawlee": crawlee, "crawl4ai": crawl4ai}[
                        args.provider
                    ](case, folder, policy)
        except Exception as exc:
            result = {
                "text": "",
                "outcome": "error",
                "reason": f"{type(exc).__name__}: {str(exc)[:800]}",
            }
        if policy.cf:
            result.update(outcome="cloudflare_skip", text="")
        elif result.get("status") in {401, 403}:
            result.update(outcome="access_wall", text="")
        result.update(
            provider=args.provider,
            library_elapsed_s=time.monotonic() - start,
            browser_pages=policy.pages,
            browser_launches=policy.launches,
            browser_launch_ms=policy.launch_ms,
            request_count=policy.count,
            versions={
                name: importlib.metadata.version(name)
                for name in (
                    ["prism-crawler", "playwright"]
                    if args.provider == "prism"
                    else [args.provider, "playwright"]
                )
            },
        )
        save(folder / "result.json", result)
    print(
        json.dumps(
            {
                k: result.get(k)
                for k in ["provider", "outcome", "reason", "browser_pages", "library_elapsed_s"]
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["prism", "crawlee", "crawl4ai"], required=True)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-private", action="store_true")
    asyncio.run(run(parser.parse_args()))
