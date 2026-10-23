"""Local-only fresh reruns of the frozen 50-site development set."""

import argparse
import asyncio
import gzip
import hashlib
import json
import random
import shutil
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from benchmarks.provider_live import BenchmarkNetwork, quality_review, write_json
from benchmarks.real50 import SOURCE_ROOT, SiteCase, runtime_digest
from optimal_crawler import crawler as crawler_module
from optimal_crawler.browser import Browser
from optimal_crawler.crawler import Crawler
from optimal_crawler.models import CrawlConfig, Document, Extraction, FetchResult, Outcome
from optimal_crawler.network import Network
from optimal_crawler.urls import canonicalize


async def run(args):
    root = args.output
    root.mkdir(parents=True, exist_ok=True)
    assert not (root / "phase.json").exists(), "Use a fresh output directory"
    cases = [SiteCase(**c) for c in json.loads(Path("benchmarks/real50.json").read_text())]
    if args.case_ids:
        assert set(args.case_ids) <= {c.id for c in cases}
        cases = [c for c in cases if c.id in args.case_ids]
    profile = json.loads(Path("benchmarks/adaptive-profile.json").read_text())
    digest = runtime_digest()
    phase = {"runtime_sha256": digest, "source_root": str(SOURCE_ROOT),
             "started_at": datetime.now(UTC).isoformat(), "cases": len(cases),
             "repetitions": args.repetitions, "benchmark_ignore_robots": args.benchmark_ignore_robots,
             "page_deadline_seconds": 90, "profile": profile, "provider_calls": 0,
             "manifest_sha256": hashlib.sha256(Path("benchmarks/real50.json").read_bytes()).hexdigest(),
             "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
             "claim": "Fresh local rerun, fixed development URLs and checks. Provider baseline is historical."}
    write_json(root / "phase.json", phase)
    shutil.copyfile("benchmarks/real50.json", root / "manifest.json")
    shutil.copyfile("benchmarks/real50-review.json", root / "quality-contract.json")
    shutil.copyfile(__file__, root / "harness.py")
    shutil.copytree(SOURCE_ROOT, root / "source", ignore=shutil.ignore_patterns("__pycache__"))
    bodies = root / "bodies"
    bodies.mkdir()
    raw, browser, process = Network._raw, Browser.fetch, Crawler._process
    by_url = {canonicalize(c.url): c for c in cases}
    for repetition in range(args.repetitions):
        assert runtime_digest() == digest, "Source changed during run"
        folder = root / f"run-{repetition}"
        folder.mkdir()
        captures, pending_bodies, rows = [], {}, []

        def capture(result, role, parent, captures=captures, pending_bodies=pending_bodies):
            sha = hashlib.sha256(result.body).hexdigest() if result.body else None
            if sha:
                pending_bodies[sha] = result.body
            captures.append({"url": result.url, "parent": parent, "role": role,
                             "status": result.status, "outcome": str(result.outcome),
                             "body_sha256": sha, "headers": {k: v for k, v in result.headers.items()
                                                              if k in {"content-type", "location", "retry-after"}}})

        async def observed_raw(instance, url, headers, role, parent, asset=False,
                               *, method="GET", redirect=False, capture=capture):
            result = await raw(instance, url, headers, role, parent, asset,
                               method=method, redirect=redirect)
            capture(result, role, parent)
            return result

        async def observed_browser(instance, url, capture=capture):
            result = await browser(instance, url)
            capture(result, "rendered_dom", url)
            return result

        async def observed_process(instance, url, repetition=repetition, folder=folder,
                                   rows=rows, pending_bodies=pending_bodies):
            case = by_url[url]
            start = time.perf_counter()
            try:
                async with asyncio.timeout(90):
                    result = await process(instance, url)
            except TimeoutError:
                result = (Document(url, url, Outcome.ERROR, "adaptive",
                                   reason="90-second benchmark page deadline"),
                          Extraction(), FetchResult(url, outcome=Outcome.ERROR))
            elapsed = (time.perf_counter() - start) * 1000
            result[0].elapsed_ms = elapsed
            document = asdict(result[0])
            row = {"provider": "ours", "id": case.id, "repetition": repetition,
                   "attempted": True, "elapsed_ms": elapsed, "freshness": "live",
                   "document": document, **quality_review(case, document)}
            row["live_passed"] = row["readable_check"]["passed"]
            rows.append(row)
            write_json(folder / ("ours-" + case.id + ".json"), row)
            # Artifact compression is outside the measured page operation.
            for sha, body in pending_bodies.items():
                if not (bodies / (sha + ".gz")).exists():
                    await asyncio.to_thread((bodies / (sha + ".gz")).write_bytes,
                                            gzip.compress(body, mtime=0))
            pending_bodies.clear()
            print(json.dumps({"run": repetition, "id": case.id, "passed": row["live_passed"],
                              "outcome": str(result[0].outcome), "ms": round(elapsed),
                              "reason": result[0].reason}), flush=True)
            return result

        ordered = cases.copy()
        random.Random(230026 + repetition).shuffle(ordered)
        config = CrawlConfig(db=folder / "ours.db", max_pages=len(cases), max_requests=2000,
                             workers=1, browser_concurrency=1, min_delay=0.5, timeout=15,
                             retries=1, render_wait=3, discover=False, follow_links=False,
                             decision_provider="none", run_timeout=7200, **profile)
        network_type = BenchmarkNetwork if args.benchmark_ignore_robots else Network
        with patch.object(crawler_module, "Network", network_type), \
             patch.object(Crawler, "_process", observed_process), \
             patch.object(Network, "_raw", observed_raw), \
             patch.object(Browser, "fetch", observed_browser):
            async with Crawler(config) as crawler:
                summary = await crawler.run([c.url for c in ordered])
                write_json(folder / "ours-events.json", {
                    "summary": summary, "requests": crawler.store.events("requests", crawler.run_id),
                    "attempts": crawler.store.events("attempts", crawler.run_id),
                    "captures": captures, "config": config.to_dict()})
        assert runtime_digest() == digest
        assert len(rows) == len(cases)
        write_json(folder / "finished.json", {"rows": len(rows), "passed": sum(r["live_passed"] for r in rows)})
    write_json(root / "finished.json", {"rows": len(cases) * args.repetitions,
                                        "finished_at": datetime.now(UTC).isoformat()})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--case-ids", nargs="+")
    parser.add_argument("--benchmark-ignore-robots", action="store_true")
    asyncio.run(run(parser.parse_args()))
