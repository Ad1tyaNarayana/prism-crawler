"""Guarded live diagnostics, excluded from comparison timing."""

import argparse
import asyncio
import json
import time
from pathlib import Path
from unittest.mock import patch

from optimal_crawler.browser import Browser
from optimal_crawler.crawler import Crawler
from optimal_crawler.models import CrawlConfig, Strategy
from optimal_crawler.network import Network

from .real50 import SiteCase, score, write_json


async def run(args):
    cases = [SiteCase(**c) for c in json.loads(Path("benchmarks/real50.json").read_text())
             if c["id"] in args.ids]
    if "exa" in args.ids:
        cases.append(SiteCase("exa", "supplemental", "https://exa.ai/", 300,
                              [["exa"], ["search"], ["web", "api"]]))
    output = Path("reports/technical/development") / str(time.time_ns())
    output.mkdir(parents=True)
    events, rendered = [], []
    original_fetch, original_browser, original_start = Network.fetch, Browser.fetch, Browser._start

    async def observed_fetch(network, url, **kwargs):
        result = await original_fetch(network, url, **kwargs)
        item = {"url": url, "final_url": result.url, "role": kwargs.get("role"),
                "outcome": str(result.outcome), "reason": result.reason,
                "status": result.status, "bytes": len(result.body), "headers": result.headers,
                "elapsed_ms": result.elapsed_ms}
        if result.body:
            name = f"body-{len(events)}.txt"
            (output / name).write_text(result.text)
            item["body"] = name
        events.append(item)
        return result

    async def observed_browser(browser, url):
        result = await original_browser(browser, url)
        rendered.append({"url": url, "html": result.text, "outcome": str(result.outcome),
                         "reason": result.reason})
        return result

    async def observed_start(browser):
        await original_start(browser)
        if getattr(browser, "_diagnostic_hook", False):
            return
        browser._diagnostic_hook = True
        create_context = browser._browser.new_context

        async def observed_context(**kwargs):
            context = await create_context(**kwargs)
            if args.trace_fetch:
                await context.add_init_script("""(() => {
                    const original = window.fetch;
                    window.fetch = async (...args) => {
                        const url = String(args[0]?.url || args[0]);
                        if (!url.includes('favicon')) return original(...args);
                        console.warn('FETCH_TRACE start', url, JSON.stringify(args[1]));
                        try {
                            const response = await original(...args);
                            console.warn('FETCH_TRACE response', response.status, response.url);
                            return response;
                        } catch (error) {
                            console.warn('FETCH_TRACE error', String(error));
                            throw error;
                        }
                    };
                })()""")

            async def page_created(page):
                page.on("pageerror", lambda error: events.append({"pageerror": str(error), "page": page.url}))
                page.on("console", lambda msg: events.append({"console": msg.text[:3000], "page": page.url})
                        if msg.type in {"error", "warning"} else None)
                page.on("requestfailed", lambda req: events.append(
                    {"failed_request": req.url, "failure": req.failure, "resource": req.resource_type}))
                if args.trace_fetch:
                    session = await context.new_cdp_session(page)
                    await session.send("Network.enable")
                    session.on("Network.loadingFailed", lambda event: events.append({"cdp_failed": event}))
                    session.on("Network.requestWillBeSent", lambda event: events.append({"cdp_request": {
                        "id": event["requestId"], "url": event["request"]["url"],
                        "method": event["request"]["method"]}})
                        if "favicon" in event["request"]["url"] else None)
            context.on("page", page_created)
            close_context = context.close

            async def observed_close(*args, **kwargs):
                for page in context.pages:
                    try:
                        visible = await page.locator("body").inner_text(timeout=1000)
                        events.append({"visible_text": visible, "page": page.url})
                        events.append({"browser_state": await page.evaluate("""() => ({
                            online: navigator.onLine, bodyClass: document.body.className,
                            resources: performance.getEntriesByType('resource').map(r => ({
                                name: r.name, type: r.initiatorType, duration: r.duration
                            }))
                        })"""), "page": page.url})
                        await page.screenshot(path=str(output / f"page-{len(events)}.png"))
                    except Exception as error:
                        events.append({"snapshot_error": str(error)[:200]})
                return await close_context(*args, **kwargs)

            context.close = observed_close
            return context

        browser._browser.new_context = observed_context

    config = CrawlConfig(db=output / "crawl.db", strategy=Strategy(args.strategy),
                         max_pages=len(cases), max_requests=1500, workers=4, browser_concurrency=4,
                         discover=False, follow_links=False, min_delay=0.5, timeout=args.timeout,
                         render_wait=3, run_timeout=600, retries=1, max_bytes=args.max_bytes,
                         browser_timeout=args.browser_timeout,
                         asset_origins=tuple(args.asset_origin), load_styles=args.styles)
    with patch.object(Network, "fetch", observed_fetch), patch.object(Browser, "fetch", observed_browser), \
            patch.object(Browser, "_start", observed_start):
        async with Crawler(config) as crawler:
            summary = await crawler.run([c.url for c in cases])
            docs = crawler.store.documents(crawler.run_id)
            record = {"kind": "development only", "summary": summary, "config": config.to_dict(),
                      "documents": docs, "checks": score(cases, docs), "rendered": rendered,
                      "events": events, "requests": crawler.store.events("requests", crawler.run_id)}
    write_json(output / "results.json", record)
    for check in record["checks"]:
        print(json.dumps(check), flush=True)
    print(output / "results.json", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("ids", nargs="+")
    parser.add_argument("--strategy", choices=list(Strategy), default="adaptive")
    parser.add_argument("--asset-origin", action="append", default=[])
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--browser-timeout", type=float, default=90)
    parser.add_argument("--max-bytes", type=int, default=16_000_000)
    parser.add_argument("--styles", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--trace-fetch", action="store_true")
    asyncio.run(run(parser.parse_args()))
