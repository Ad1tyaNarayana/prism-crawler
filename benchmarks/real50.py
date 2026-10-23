"""A frozen, bounded live comparison. Never overwrites completed run evidence."""

import argparse
import asyncio
import gzip
import hashlib
import json
import os
import platform
import shutil
import statistics
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

import optimal_crawler
from optimal_crawler.browser import Browser
from optimal_crawler.crawler import Crawler
from optimal_crawler.models import CrawlConfig, Strategy
from optimal_crawler.network import Network
from optimal_crawler.urls import canonicalize

MANIFEST = Path("benchmarks/real50.json")
SOURCE_ROOT = Path(optimal_crawler.__file__).resolve().parent


@dataclass(frozen=True)
class SiteCase:
    id: str
    category: str
    url: str
    min_chars: int
    groups: list[list[str]]


def normalize(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def runtime_digest():
    digest = hashlib.sha256()
    for path in sorted(SOURCE_ROOT.glob("*.py")):
        digest.update(str(Path("src/optimal_crawler") / path.name).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def score(cases, documents):
    by_url = {doc["url"]: doc for doc in documents}
    checked = []
    for case in cases:
        doc = by_url.get(canonicalize(case.url), {})
        content = normalize(doc.get("text", ""))
        missing = [group for group in case.groups if not any(normalize(m) in content for m in group)]
        short = len(doc.get("text", "")) < case.min_chars
        passed = doc.get("outcome") == "ok" and not missing and not short
        outcome = doc.get("outcome", "missing")
        if passed:
            failure = "none"
        elif outcome == "ok":
            failure = "content_check_failed"
        elif outcome in {"robots_denied", "robots_unavailable", "access_wall"}:
            failure = outcome
        elif outcome == "out_of_scope":
            failure = "scope_restriction"
        else:
            failure = outcome
        checked.append({"id": case.id, "category": case.category, "url": case.url,
                        "passed": passed, "failure": failure, "missing": missing,
                        "too_short": short, "chars": len(doc.get("text", "")),
                        "outcome": outcome, "method": doc.get("method", ""),
                        "parser": doc.get("parser", ""), "reason": doc.get("reason", ""),
                        "final_url": doc.get("final_url", ""),
                        "elapsed_ms": doc.get("elapsed_ms", 0)})
    return checked


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def aggregate(records):
    result = {}
    for strategy in Strategy:
        group = [r for r in records if r["strategy"] == strategy]
        if not group:
            continue
        durations = [r["summary"]["elapsed_ms"] for r in group]
        result[str(strategy)] = {
            "runs": len(group), "content_passed": [sum(c["passed"] for c in r["checks"]) for r in group],
            "claimed_ok": [sum(d["outcome"] == "ok" for d in r["documents"]) for r in group],
            "content_total": 50, "median_ms": statistics.median(durations),
            "min_ms": min(durations), "max_ms": max(durations),
            "requests": [len(r["requests"]) for r in group],
            "browser_pages": [r["summary"]["browser_pages"] for r in group],
            "bytes": [r["summary"]["bytes"] for r in group],
            "failures": dict(Counter(c["failure"] for r in group for c in r["checks"] if not c["passed"])),
            "per_site": {case["id"]: {
                "passes": sum(next(c for c in r["checks"] if c["id"] == case["id"])["passed"] for r in group),
                "failures": dict(Counter(next(c for c in r["checks"] if c["id"] == case["id"])["failure"] for r in group)),
            } for case in group[0]["checks"]},
        }
    return result


async def run(phase, repetitions, output_root=Path("reports/real50"), profile=None):
    cases = [SiteCase(**entry) for entry in json.loads(MANIFEST.read_text())]
    assert len(cases) == len({case.id for case in cases}) == 50
    assert len({urlsplit(case.url).hostname for case in cases}) == 50
    assert set(Counter(case.category for case in cases).values()) == {5}
    output = output_root / phase
    output.mkdir(parents=True, exist_ok=True)
    bodies = output / "bodies"
    bodies.mkdir(exist_ok=True)
    source_hash = runtime_digest()
    manifest_hash = hashlib.sha256(MANIFEST.read_bytes()).hexdigest()
    harness_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    settings = json.loads(profile.read_text()) if profile else {}
    assert set(settings) <= {"browser_timeout", "max_bytes", "load_styles", "asset_origins"}
    metadata_path = output / "phase.json"
    metadata = {"phase": phase, "started_at": datetime.now(UTC).isoformat(),
                "runtime_sha256": source_hash, "manifest_sha256": manifest_hash,
                "harness_sha256": harness_hash, "repetitions": repetitions, "profile": settings,
                "implementation_path": str(SOURCE_ROOT),
                "python": platform.python_version(), "platform": platform.platform(),
                "logical_cpus": os.cpu_count(), "load_average": os.getloadavg(),
                "dependencies": {name: version(name) for name in
                                 ("aiohttp", "playwright", "trafilatura", "protego", "lxml")}}
    if metadata_path.exists():
        metadata = json.loads(metadata_path.read_text())
        assert metadata["runtime_sha256"] == source_hash, "Cannot mix runtime versions within a phase"
        assert metadata["manifest_sha256"] == manifest_hash, "Frozen manifest changed"
        assert metadata["harness_sha256"] == harness_hash, "Measurement harness changed"
        assert metadata["repetitions"] == repetitions
        assert metadata.get("profile", {}) == settings
    else:
        write_json(metadata_path, metadata)
        shutil.copyfile(MANIFEST, output / "manifest.json")
        shutil.copyfile(__file__, output / "harness.py")
        write_json(output / "profile.json", settings)
        snapshot = output / "source"
        snapshot.mkdir()
        for source in SOURCE_ROOT.glob("*.py"):
            shutil.copyfile(source, snapshot / source.name)

    records = []
    strategies = list(Strategy)
    original_raw, original_browser = Network._raw, Browser.fetch
    for repetition in range(repetitions):
        order = strategies[repetition % 3:] + strategies[:repetition % 3]
        offset = (repetition * 11) % len(cases)
        urls = [case.url for case in cases[offset:] + cases[:offset]]
        for strategy in order:
            record_path = output / f"run-{repetition}-{strategy}.json"
            if record_path.exists():
                records.append(json.loads(record_path.read_text()))
                continue
            assert runtime_digest() == source_hash, "Runtime edited during a frozen phase"
            config = CrawlConfig(
                db=output / f"run-{repetition}-{strategy}-{time.time_ns()}.db",
                strategy=strategy, max_pages=50, max_requests=1500, workers=4,
                browser_concurrency=4, discover=False, follow_links=False,
                min_delay=0.5, timeout=15, render_wait=3, run_timeout=600,
                retries=1, decision_provider="none",
                **settings,
            )
            captures = []

            def capture(result, role, parent, captures=captures):
                digest = hashlib.sha256(result.body).hexdigest()
                if result.body:
                    target = bodies / (digest + ".gz")
                    if not target.exists():
                        target.write_bytes(gzip.compress(result.body, compresslevel=1, mtime=0))
                captures.append({"url": result.url, "role": role, "parent": parent,
                                 "status": result.status, "outcome": str(result.outcome),
                                 "reason": result.reason, "body_sha256": digest if result.body else None,
                                 "headers": {k: v for k, v in result.headers.items()
                                             if k in {"content-type", "location", "etag", "last-modified"}}})

            async def observed_raw(network, url, headers, role, parent, asset=False, *, method="GET"):
                result = await original_raw(network, url, headers, role, parent, asset, method=method)
                if role != "browser_asset":
                    capture(result, role, parent)
                return result

            async def observed_browser(browser, url):
                result = await original_browser(browser, url)
                capture(result, "rendered_dom", url)
                return result

            started = datetime.now(UTC).isoformat()
            load = os.getloadavg()
            print(json.dumps({"event": "start", "phase": phase, "repetition": repetition,
                              "strategy": str(strategy), "at": started, "db": str(config.db)}), flush=True)
            with patch.object(Network, "_raw", observed_raw), patch.object(Browser, "fetch", observed_browser):
                async with Crawler(config) as crawler:
                    summary = await crawler.run(urls)
                    documents = crawler.store.documents(crawler.run_id)
                    requests = crawler.store.events("requests", crawler.run_id)
                    attempts = crawler.store.events("attempts", crawler.run_id)
            assert runtime_digest() == source_hash, "Runtime edited during a measured run"
            record = {"phase": phase, "repetition": repetition, "strategy": str(strategy),
                      "started_at": started, "finished_at": datetime.now(UTC).isoformat(),
                      "load_average": load, "runtime_sha256": source_hash,
                      "manifest_sha256": manifest_hash, "harness_sha256": harness_hash,
                      "summary": summary, "config": config.to_dict(), "documents": documents,
                      "requests": requests, "attempts": attempts, "captures": captures,
                      "checks": score(cases, documents)}
            write_json(record_path, record)
            records.append(record)
            report = {"metadata": metadata, "results": aggregate(records)}
            write_json(output / "summary.json", report)
            print(json.dumps({"event": "finished", "repetition": repetition, **summary,
                              "content_checks": sum(c["passed"] for c in record["checks"])}), flush=True)
    print(json.dumps({"event": "phase_complete", "phase": phase, "runs": len(records)}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["baseline", "after"], required=True)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--output-root", type=Path, default=Path("reports/real50"))
    parser.add_argument("--profile", type=Path)
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("repetitions must be positive")
    asyncio.run(run(args.phase, args.repetitions, args.output_root, args.profile))
