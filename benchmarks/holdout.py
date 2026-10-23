"""Run a frozen new-site cohort once, preserving raw policy/content evidence."""

import argparse
import asyncio
import gzip
import hashlib
import json
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from benchmarks.access_diagnostics import response_evidence
from benchmarks.real50 import SOURCE_ROOT, runtime_digest
from optimal_crawler.browser import Browser
from optimal_crawler.crawler import Crawler
from optimal_crawler.models import CrawlConfig
from optimal_crawler.network import Network


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str))


async def run(args):
    root = args.output
    root.mkdir(parents=True, exist_ok=False)
    source = Path("benchmarks/holdout50.json")
    cases = json.loads(source.read_text())
    cases = [c for c in cases if c["cohort"] == args.cohort]
    if args.case_ids:
        cases = [c for c in cases if c["id"] in args.case_ids]
    digest = runtime_digest()
    save(
        root / "phase.json",
        {
            "started_at": datetime.now(UTC).isoformat(),
            "manifest_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "source_sha256": digest,
            "cohort": args.cohort,
            "case_ids": [c["id"] for c in cases],
            "robots": "enforce",
            "paid_calls": 0,
            "cache": "fresh database per site",
            "claim": "First exposure only if this cohort has never been run before; reruns are development results.",
        },
    )
    shutil.copyfile(source, root / "manifest.json")
    shutil.copyfile(__file__, root / "harness.py")
    shutil.copytree(SOURCE_ROOT, root / "source", ignore=shutil.ignore_patterns("__pycache__"))
    rows = []
    for case in cases:
        assert runtime_digest() == digest, "Do not edit production source during a cohort run"
        folder = root / case["id"]
        folder.mkdir()
        captures, bodies = [], {}
        raw, browser_fetch = Network._raw, Browser.fetch

        def capture(result, role, bodies=bodies, captures=captures):
            sha = hashlib.sha256(result.body).hexdigest() if result.body else None
            if sha:
                bodies[sha] = result.body
            captures.append(
                {
                    "url": result.url,
                    "role": role,
                    "status": result.status,
                    "outcome": str(result.outcome),
                    "reason": result.reason,
                    "body_sha256": sha,
                    "evidence": response_evidence(result),
                }
            )

        async def observed_raw(instance, *a, raw=raw, capture=capture, **kw):
            result = await raw(instance, *a, **kw)
            capture(result, a[2] if len(a) > 2 else kw.get("role", "unknown"))
            return result

        async def observed_browser(instance, url, browser_fetch=browser_fetch, capture=capture):
            result = await browser_fetch(instance, url)
            capture(result, "rendered_dom")
            return result

        config = CrawlConfig(
            db=folder / "crawl.db",
            redirect_origins=tuple(args.redirect_origin),
            max_pages=1,
            max_requests=140,
            workers=1,
            browser_concurrency=1,
            min_delay=0.5,
            timeout=12,
            browser_timeout=35,
            run_timeout=55,
            retries=0,
            discover=False,
            follow_links=False,
            render_wait=1,
        )
        started_at = datetime.now(UTC).isoformat()
        start = time.perf_counter()
        with (
            patch.object(Network, "_raw", observed_raw),
            patch.object(Browser, "fetch", observed_browser),
        ):
            async with Crawler(config) as crawler:
                summary = await crawler.run([case["url"]])
                docs = crawler.store.documents(crawler.run_id)
                requests = crawler.store.events("requests", crawler.run_id)
                attempts = crawler.store.events("attempts", crawler.run_id)
        elapsed = (time.perf_counter() - start) * 1000
        doc = (
            docs[0]
            if docs
            else {
                "outcome": "error",
                "text": "",
                "reason": "No document before run deadline",
                "status": 0,
            }
        )
        cf = [c for c in captures if c["evidence"]["kind"].startswith("cloudflare_")]
        text = doc["text"].casefold()
        missing = [g for g in case["groups"] if not any(w.casefold() in text for w in g)]
        passed = doc["outcome"] == "ok" and len(doc["text"]) >= case["min_chars"] and not missing
        row = {
            "case": case,
            "started_at": started_at,
            "completed_at": datetime.now(UTC).isoformat(),
            "elapsed_ms": elapsed,
            "document": doc,
            "passed": passed,
            "skipped_cloudflare": bool(cf),
            "missing_groups": missing,
            "summary": summary,
            "requests": requests,
            "attempts": attempts,
            "captures": captures,
        }
        for sha, body in bodies.items():
            (folder / (sha + ".gz")).write_bytes(gzip.compress(body, mtime=0))
        save(folder / "result.json", row)
        rows.append(
            {
                "id": case["id"],
                "passed": passed,
                "skipped_cloudflare": bool(cf),
                "outcome": doc["outcome"],
                "status": doc["status"],
                "elapsed_ms": elapsed,
                "reason": doc["reason"],
                "missing_groups": missing,
            }
        )
        save(root / "progress.json", rows)
        print(json.dumps(rows[-1]), flush=True)
    save(
        root / "summary.json",
        {
            "finished_at": datetime.now(UTC).isoformat(),
            "total": len(rows),
            "passed": sum(r["passed"] for r in rows),
            "skipped_cloudflare": sum(r["skipped_cloudflare"] for r in rows),
            "rows": rows,
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cohort", choices=["initial", "reserved"], required=True)
    parser.add_argument("--case-ids", nargs="+")
    parser.add_argument("--redirect-origin", action="append", default=[])
    asyncio.run(run(parser.parse_args()))
