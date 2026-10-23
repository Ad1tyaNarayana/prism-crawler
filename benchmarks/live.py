import asyncio
import json
import time
from pathlib import Path

from optimal_crawler.crawler import Crawler
from optimal_crawler.models import CrawlConfig, Strategy

URLS = [
    "https://quotes.toscrape.com/",
    "https://quotes.toscrape.com/js/",
    "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html",
    "https://docs.python.org/3/library/asyncio.html",
    "https://httpbin.org/html",
    "https://www.scrapethissite.com/pages/simple/",
]

MARKERS = {
    URLS[0]: ["Albert Einstein", "J.K. Rowling"],
    URLS[1]: ["Albert Einstein", "J.K. Rowling"],
    URLS[2]: ["A Light in the Attic", "Silverstein"],
    URLS[3]: ["asyncio", "async/await"],
    URLS[4]: ["Herman Melville", "Availing himself"],
    URLS[5]: ["Afghanistan", "Zimbabwe"],
}


async def main():
    output = Path("reports/live")
    output.mkdir(parents=True, exist_ok=True)
    summaries = []
    for strategy in Strategy:
        config = CrawlConfig(db=output / f"{strategy}-{time.time_ns()}.db", strategy=strategy,
                             max_pages=len(URLS), max_requests=100, discover=False,
                             follow_links=False, workers=4, min_delay=0.5, timeout=15,
                             run_timeout=180, render_wait=3)
        async with Crawler(config) as crawler:
            summary = await crawler.run(URLS)
            documents = crawler.store.documents(crawler.run_id)
            requests = crawler.store.events("requests", crawler.run_id)
            attempts = crawler.store.events("attempts", crawler.run_id)
            checks = [{"url": d["url"], "outcome": d["outcome"],
                       "passed": d["outcome"] == "ok" and all(
                           marker in d["title"] + "\n" + d["text"] for marker in MARKERS[d["url"]]),
                       "missing": [marker for marker in MARKERS[d["url"]]
                                   if marker not in d["title"] + "\n" + d["text"]]}
                      for d in documents]
            summary["content_checks_passed"] = sum(c["passed"] for c in checks)
            record = {"summary": summary, "config": config.to_dict(), "documents": documents,
                      "requests": requests, "attempts": attempts, "content_checks": checks}
            (output / f"{strategy}.json").write_text(json.dumps(record, indent=2, ensure_ascii=False))
        summaries.append(summary)
        print(json.dumps(summary), flush=True)
    (output / "summary.json").write_text(json.dumps({"kind": "single-run smoke test, not accuracy ground truth",
                                                     "urls": URLS, "runs": summaries}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
