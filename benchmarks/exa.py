"""Supplemental Exa website checks. Does not call the Exa search API."""

import argparse
import asyncio
import gzip
import hashlib
import json
import os
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from optimal_crawler.browser import Browser
from optimal_crawler.crawler import Crawler
from optimal_crawler.models import CrawlConfig, Strategy
from optimal_crawler.network import Network

from .real50 import SOURCE_ROOT, SiteCase, runtime_digest, score, write_json


async def run(output: Path):
    output.mkdir(parents=True, exist_ok=True)
    manifest = Path("benchmarks/exa.json")
    cases = [SiteCase(**row) for row in json.loads(manifest.read_text())]
    digest = runtime_digest()
    signature = {"runtime_sha256": digest,
                 "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
                 "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    metadata_path = output / "phase.json"
    if metadata_path.exists():
        assert json.loads(metadata_path.read_text()) == signature
    else:
        write_json(metadata_path, signature)
        shutil.copyfile(manifest, output / "manifest.json")
        shutil.copyfile(__file__, output / "harness.py")
        shutil.copytree(SOURCE_ROOT, output / "source", ignore=shutil.ignore_patterns("__pycache__"))
    bodies = output / "bodies"
    bodies.mkdir(exist_ok=True)
    original_raw, original_browser = Network._raw, Browser.fetch
    for repetition in range(5):
        strategies = list(Strategy)
        for strategy in strategies[repetition % 3:] + strategies[:repetition % 3]:
            path = output / f"run-{repetition}-{strategy}.json"
            if path.exists():
                continue
            assert runtime_digest() == digest
            captures = []

            def capture(result, role, captures=captures):
                body_hash = hashlib.sha256(result.body).hexdigest() if result.body else None
                if body_hash:
                    (bodies / (body_hash + ".gz")).write_bytes(gzip.compress(result.body, mtime=0))
                captures.append({"url": result.url, "role": role, "status": result.status,
                                 "outcome": str(result.outcome), "body_sha256": body_hash})

            async def observed_raw(network, url, headers, role, parent, asset=False, *, method="GET"):
                result = await original_raw(network, url, headers, role, parent, asset, method=method)
                if role != "browser_asset":
                    capture(result, role)
                return result

            async def observed_browser(browser, url):
                result = await original_browser(browser, url)
                capture(result, "rendered_dom")
                return result

            config = CrawlConfig(db=output / f"run-{repetition}-{strategy}-{time.time_ns()}.db",
                                 strategy=strategy, max_pages=len(cases), max_requests=500,
                                 workers=4, browser_concurrency=4, min_delay=0.5, timeout=15,
                                 discover=False, follow_links=False, retries=1,
                                 decision_provider="none")
            started = datetime.now(UTC).isoformat()
            print(json.dumps({"event": "start", "repetition": repetition, "strategy": str(strategy)}), flush=True)
            with patch.object(Network, "_raw", observed_raw), patch.object(Browser, "fetch", observed_browser):
                async with Crawler(config) as crawler:
                    summary = await crawler.run([case.url for case in cases[repetition:] + cases[:repetition]])
                    docs = crawler.store.documents(crawler.run_id)
                    record = {"strategy": str(strategy), "repetition": repetition,
                              "started_at": started, "finished_at": datetime.now(UTC).isoformat(),
                              "config": config.to_dict(), "summary": summary, "documents": docs,
                              "checks": score(cases, docs), "captures": captures,
                              "requests": crawler.store.events("requests", crawler.run_id),
                              "attempts": crawler.store.events("attempts", crawler.run_id),
                              "load_average": os.getloadavg(), **signature}
            assert runtime_digest() == digest
            write_json(path, record)
            print(json.dumps({"event": "finished", "repetition": repetition, **summary,
                              "checks": sum(check["passed"] for check in record["checks"])}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("reports/technical/exa"))
    args = parser.parse_args()
    asyncio.run(run(args.output))
