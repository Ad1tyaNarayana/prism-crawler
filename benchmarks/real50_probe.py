"""Development rechecks, explicitly excluded from measured comparison runs."""

import argparse
import asyncio
import json
import time
from pathlib import Path
from unittest.mock import patch

import optimal_crawler
from optimal_crawler.browser import Browser
from optimal_crawler.crawler import Crawler
from optimal_crawler.models import CrawlConfig

from .real50 import SiteCase, score, write_json


async def main(ids):
    cases = [SiteCase(**c) for c in json.loads(Path("benchmarks/real50.json").read_text()) if c["id"] in ids]
    output = Path("reports/real50/development") / str(time.time_ns())
    output.mkdir(parents=True)
    rendered = []
    original = Browser.fetch

    async def observed(browser, url):
        result = await original(browser, url)
        rendered.append({"url": url, "outcome": str(result.outcome), "reason": result.reason,
                         "html": result.text})
        return result

    config = CrawlConfig(db=output / "crawl.db", max_pages=len(cases), max_requests=1500,
                         workers=4, browser_concurrency=4, discover=False, follow_links=False,
                         min_delay=0.5, timeout=15, render_wait=3, run_timeout=600, retries=1)
    with patch.object(Browser, "fetch", observed):
        async with Crawler(config) as crawler:
            summary = await crawler.run([c.url for c in cases])
            docs = crawler.store.documents(crawler.run_id)
            record = {"kind": "development recheck; excluded from comparison", "summary": summary,
                      "implementation": optimal_crawler.__file__, "config": config.to_dict(),
                      "documents": docs, "checks": score(cases, docs), "rendered": rendered,
                      "requests": crawler.store.events("requests", crawler.run_id)}
    write_json(output / "results.json", record)
    for check in record["checks"]:
        print(json.dumps(check), flush=True)
    print(output / "results.json", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("ids", nargs="+")
    args = parser.parse_args()
    asyncio.run(main(set(args.ids)))
