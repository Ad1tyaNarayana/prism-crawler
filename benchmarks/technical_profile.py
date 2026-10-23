"""One extra profiled run, explicitly excluded from the comparison."""

import asyncio
import cProfile
import json
import pstats
import time
from pathlib import Path

from optimal_crawler.crawler import Crawler
from optimal_crawler.models import CrawlConfig

from .real50 import runtime_digest, write_json


async def run():
    root = Path("reports/technical-final")
    profile = json.loads(Path("benchmarks/technical-profile.json").read_text())
    cases = json.loads(Path("benchmarks/real50.json").read_text())
    config = CrawlConfig(db=root / f"profile-{time.time_ns()}.db", max_pages=50, max_requests=1500,
                         workers=4, browser_concurrency=4, min_delay=0.5, timeout=15, render_wait=3,
                         retries=1, discover=False, follow_links=False, **profile)
    async with Crawler(config) as crawler:
        summary = await crawler.run([c["url"] for c in cases])
        write_json(root / "profile-run.json", {"excluded_from_comparison": True, "summary": summary,
                   "runtime_sha256": runtime_digest(), "config": config.to_dict(),
                   "documents": crawler.store.documents(crawler.run_id)})


def main():
    root = Path("reports/technical-final")
    profiler = cProfile.Profile()
    profiler.runcall(asyncio.run, run())
    profiler.dump_stats(root / "profile.pstats")
    with (root / "profile-summary.txt").open("w") as stream:
        stats = pstats.Stats(profiler, stream=stream)
        stats.sort_stats("tottime").print_stats(25)
    wait = sum(row[2] for key, row in stats.stats.items() if "method 'control' of 'select.kqueue'" in key[2])
    write_json(root / "profile-measurement.json", {"excluded_from_comparison": True,
               "scope": "Main event-loop thread; parser worker-thread CPU is not aggregated",
               "total_seconds": stats.total_tt, "event_loop_poll_seconds": wait})


if __name__ == "__main__":
    main()
