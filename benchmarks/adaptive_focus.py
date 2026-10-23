"""Frozen adaptive-only checks, retaining original 50-site requirements."""

import argparse
import asyncio
import gzip
import hashlib
import json
import shutil
import time
from pathlib import Path
from unittest.mock import patch

from benchmarks.real50 import SOURCE_ROOT, SiteCase, runtime_digest, score
from benchmarks.real50_review import reviewed_checks
from optimal_crawler.browser import Browser
from optimal_crawler.crawler import Crawler
from optimal_crawler.models import CrawlConfig
from optimal_crawler.network import Network


def select_cases(cases, case_ids):
    """Select original cases without replacing URLs or weakening checks."""
    if not case_ids:
        return cases
    unknown = set(case_ids) - {case["id"] for case in cases}
    if unknown:
        raise ValueError(f"Unknown case IDs: {', '.join(sorted(unknown))}")
    return [case for case in cases if case["id"] in set(case_ids)]


async def run(args):
    cases = json.loads(Path("benchmarks/real50.json").read_text())
    cases = select_cases(cases, getattr(args, "case_ids", None))
    root = Path("reports/adaptive") / args.phase
    root.mkdir(parents=True, exist_ok=True)
    bodies = root / "bodies"
    bodies.mkdir(exist_ok=True)
    if args.failures_only:
        ids = {
            r["id"]
            for r in json.loads(Path("reports/continuation/comparison.json").read_text())[
                "remaining"
            ]
        }
        cases = [c for c in cases if c["id"] in ids]
    cases = [SiteCase(**c) for c in cases]
    fingerprint = runtime_digest()
    metadata = {
        "runtime_sha256": fingerprint,
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "manifest_sha256": hashlib.sha256(Path("benchmarks/real50.json").read_bytes()).hexdigest(),
        "phase": args.phase,
        "repetitions": args.repetitions,
        "cases": len(cases),
        "strategy": "adaptive",
    }
    assert not (root / "phase.json").exists(), "Choose a fresh phase to preserve evidence"
    (root / "phase.json").write_text(json.dumps(metadata, indent=2))
    (root / "manifest.json").write_text(json.dumps([c.__dict__ for c in cases], indent=2))
    shutil.copytree(SOURCE_ROOT, root / "source", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copyfile(__file__, root / "harness.py")
    raw, browser = Network._raw, Browser.fetch
    profile = json.loads(args.profile.read_text())
    (root / "profile.json").write_text(json.dumps(profile, indent=2))
    for repetition in range(args.repetitions):
        assert runtime_digest() == fingerprint
        captures = []

        def capture(result, role, parent, captures=captures):
            sha = hashlib.sha256(result.body).hexdigest() if result.body else None
            if sha:
                (bodies / (sha + ".gz")).write_bytes(gzip.compress(result.body, mtime=0))
            captures.append(
                {
                    "url": result.url,
                    "role": role,
                    "parent": parent,
                    "status": result.status,
                    "outcome": str(result.outcome),
                    "reason": result.reason,
                    "body_sha256": sha,
                    "headers": {
                        k: v
                        for k, v in result.headers.items()
                        if k in {"content-type", "location", "retry-after", "server"}
                    },
                    "sets_cookie": "set-cookie" in result.headers,
                }
            )

        async def observed_raw(
            instance, url, headers, role, parent, asset=False, *, method="GET", redirect=False
        ):
            result = await raw(
                instance, url, headers, role, parent, asset, method=method, redirect=redirect
            )
            if role != "browser_asset":
                capture(result, role, parent)
            return result

        async def observed_browser(instance, url):
            result = await browser(instance, url)
            capture(result, "rendered_dom", url)
            return result

        config = CrawlConfig(
            db=root / f"run-{repetition}.db",
            max_pages=len(cases),
            max_requests=1500,
            workers=4,
            browser_concurrency=4,
            min_delay=0.5,
            timeout=15,
            retries=1,
            render_wait=3,
            discover=False,
            follow_links=False,
            decision_provider="none",
            run_timeout=600,
            **profile,
        )
        print(
            json.dumps(
                {
                    "event": "start",
                    "phase": args.phase,
                    "repetition": repetition,
                    "cases": len(cases),
                }
            ),
            flush=True,
        )
        with (
            patch.object(Network, "_raw", observed_raw),
            patch.object(Browser, "fetch", observed_browser),
        ):
            async with Crawler(config) as crawler:
                summary = await crawler.run([c.url for c in cases])
                docs = crawler.store.documents(crawler.run_id)
                result = {
                    **metadata,
                    "repetition": repetition,
                    "finished_at": time.time(),
                    "config": config.to_dict(),
                    "summary": summary,
                    "documents": docs,
                    "checks": score(cases, docs),
                    "captures": captures,
                    "requests": crawler.store.events("requests", crawler.run_id),
                    "attempts": crawler.store.events("attempts", crawler.run_id),
                }
                result["reviewed"] = reviewed_checks(result)
        assert runtime_digest() == fingerprint
        (root / f"run-{repetition}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2)
        )
        print(
            json.dumps(
                {
                    "event": "finished",
                    "repetition": repetition,
                    **summary,
                    "reviewed_passed": sum(c["passed"] for c in result["reviewed"]),
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--failures-only", action="store_true")
    parser.add_argument("--case-ids", nargs="+", help="Subset of original case IDs; checks unchanged")
    parser.add_argument("--profile", type=Path, default=Path("benchmarks/adaptive-profile.json"))
    asyncio.run(run(parser.parse_args()))
