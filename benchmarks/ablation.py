import asyncio
import json
import time
from pathlib import Path

from optimal_crawler.crawler import Crawler
from optimal_crawler.models import CrawlConfig

from .fixture import CASES, site
from .run import score


async def main():
    output = Path("reports/ablation")
    output.mkdir(parents=True, exist_ok=True)
    summaries = []
    async with site() as fixture:
        for provider in ("none", "laya", "systemone"):
            fixture.reset()
            config = CrawlConfig(db=output / f"{provider}-{time.time_ns()}.db", max_pages=len(CASES) + 1,
                                 max_requests=500, allow_private=True, min_delay=0.005,
                                 discover=False, follow_links=False, max_bytes=200_000,
                                 timeout=5, render_wait=1.2, decision_provider=provider,
                                 decision_mode="assist", decision_endpoint="http://127.0.0.1:8019")
            async with Crawler(config) as crawler:
                summary = await crawler.run([fixture.base + p for p in [*CASES, "/ambiguous"]])
                documents = crawler.store.documents(crawler.run_id)
                scored = score(documents, fixture.receipts)
                ambiguous = next(d for d in documents if d["url"].endswith("/ambiguous"))
                extra_pass = "forty seven spectra" in ambiguous["text"] and "Subscribe" not in ambiguous["text"]
                record = {"provider": provider, "summary": summary, "score": scored,
                          "ambiguity_passed": extra_pass, "documents": documents,
                          "decisions": crawler.store.events("decisions", crawler.run_id)}
                (output / f"{provider}.json").write_text(json.dumps(record, indent=2))
            summaries.append({"provider": provider, "summary": summary,
                              "content_passed": scored["content_passed"], "content_total": scored["content_total"],
                              "ambiguity_passed": extra_pass})
            print(json.dumps(summaries[-1]), flush=True)
    (output / "summary.json").write_text(json.dumps(summaries, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
