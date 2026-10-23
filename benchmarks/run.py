import argparse
import asyncio
import hashlib
import json
import platform
import statistics
import time
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlsplit

from optimal_crawler.crawler import Crawler
from optimal_crawler.models import CrawlConfig, Strategy

from .fixture import CASES, site


def source_digest():
    files = [*Path("src/optimal_crawler").glob("*.py"), Path("benchmarks/run.py"), Path("benchmarks/fixture.py")]
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(str(path).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def score(documents, receipts):
    by_path = {urlsplit(d["url"]).path: d for d in documents}
    cases = []
    for path, expected in CASES.items():
        document = by_path.get(path, {})
        combined = document.get("title", "") + "\n" + document.get("text", "")
        extraction = "markers" in expected
        passed = document.get("outcome") == expected.get("outcome", "ok")
        missing = [marker for marker in expected.get("markers", []) if marker not in combined]
        passed = passed and not missing and "BOILERPLATE_SENTINEL" not in combined
        cases.append({"path": path, "kind": "content" if extraction else "policy",
                      "passed": passed, "missing": missing, "outcome": document.get("outcome", "missing"),
                      "method": document.get("method", ""), "parser": document.get("parser", ""),
                      "reason": document.get("reason", ""), "chars": len(document.get("text", ""))})
    forbidden = sum(r["path"] == "/private/secret" for r in receipts)
    return {"content_passed": sum(c["passed"] for c in cases if c["kind"] == "content"),
            "content_total": sum(c["kind"] == "content" for c in cases),
            "policy_passed": sum(c["passed"] for c in cases if c["kind"] == "policy"),
            "policy_total": sum(c["kind"] == "policy" for c in cases),
            "forbidden_requests": forbidden, "origin_receipts": len(receipts), "cases": cases}


async def run(repetitions: int, output: Path, provider: str = "none", mode: str = "shadow"):
    source_hash = source_digest()
    output.mkdir(parents=True, exist_ok=True)
    results = []
    strategies = list(Strategy)
    async with site() as fixture:
        for repetition in range(repetitions):
            order = strategies[repetition % 3:] + strategies[:repetition % 3]
            for strategy in order:
                fixture.reset()
                config = CrawlConfig(
                    db=output / f"{repetition}-{strategy}-{time.time_ns()}.db", strategy=strategy,
                    max_pages=len(CASES), max_requests=500, min_delay=0.005, max_delay=1,
                    workers=3, allow_private=True, discover=True, follow_links=False,
                    max_bytes=200_000, render_wait=1.2, timeout=5, run_timeout=120,
                    decision_provider=provider if strategy == Strategy.ADAPTIVE else "none",
                    decision_mode=mode, decision_endpoint="http://127.0.0.1:8019",
                )
                async with Crawler(config) as crawler:
                    summary = await crawler.run([fixture.base + path for path in CASES])
                    documents = crawler.store.documents(crawler.run_id)
                    requests = crawler.store.events("requests", crawler.run_id)
                    attempts = crawler.store.events("attempts", crawler.run_id)
                    decisions = crawler.store.events("decisions", crawler.run_id)
                    scored = score(documents, fixture.receipts)
                    result = {"repetition": repetition, "summary": summary, "score": scored,
                              "config": config.to_dict(), "documents": documents, "requests": requests,
                              "attempts": attempts, "decisions": decisions, "receipts": fixture.receipts.copy()}
                results.append(result)
                (output / f"run-{repetition}-{strategy}.json").write_text(json.dumps(result, indent=2))
                print(json.dumps({"repetition": repetition, **summary,
                                  "content": f"{scored['content_passed']}/{scored['content_total']}",
                                  "policy": f"{scored['policy_passed']}/{scored['policy_total']}"}), flush=True)
    aggregated = {}
    for strategy in strategies:
        group = [r for r in results if r["summary"]["strategy"] == strategy]
        durations = [r["summary"]["elapsed_ms"] for r in group]
        aggregated[strategy] = {
            "runs": len(group), "median_ms": statistics.median(durations),
            "min_ms": min(durations), "max_ms": max(durations),
            "content_passed": [r["score"]["content_passed"] for r in group],
            "content_total": group[0]["score"]["content_total"],
            "policy_passed": [r["score"]["policy_passed"] for r in group],
            "policy_total": group[0]["score"]["policy_total"],
            "requests": [r["summary"]["requests"] for r in group],
            "browser_pages": [r["summary"]["browser_pages"] for r in group],
            "bytes": [r["summary"]["bytes"] for r in group],
            "receipt_match": all(r["summary"]["requests"] == r["score"]["origin_receipts"] for r in group),
            "forbidden_requests": sum(r["score"]["forbidden_requests"] for r in group),
        }
    if source_hash != source_digest():
        raise RuntimeError("Crawler sources changed during the benchmark; rerun before reporting results")
    report = {"created": time.time(), "platform": platform.platform(), "python": platform.python_version(),
              "source_sha256": source_hash,
              "dependencies": {name: version(name) for name in ["aiohttp", "playwright", "trafilatura", "protego", "lxml"]},
              "contract_sha256": hashlib.sha256(json.dumps(CASES, sort_keys=True).encode()).hexdigest(),
              "provider": provider, "mode": mode, "results": aggregated}
    (output / "summary.json").write_text(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--output", type=Path, default=Path("reports/benchmark"))
    parser.add_argument("--provider", default="none", choices=["none", "laya", "systemone"])
    parser.add_argument("--mode", default="shadow", choices=["shadow", "assist"])
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("repetitions must be positive")
    print(json.dumps(asyncio.run(run(args.repetitions, args.output, args.provider, args.mode)), indent=2))
