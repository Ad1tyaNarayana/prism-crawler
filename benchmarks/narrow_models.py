import asyncio
import hashlib
import json
import statistics
import time
from pathlib import Path

from optimal_crawler.decisions import BLOCK_QUESTION, DecisionEngine
from optimal_crawler.models import CrawlConfig


async def main():
    raw = Path("benchmarks/blocks.json").read_bytes()
    cases = json.loads(raw)
    engines = {
        "laya-multilingual": DecisionEngine(CrawlConfig(decision_provider="laya")),
        "kev-0.8b": DecisionEngine(CrawlConfig(decision_provider="systemone", decision_endpoint="http://127.0.0.1:8019")),
    }
    results = {name: [] for name in engines}
    for engine in engines.values():
        await engine.decide("An observatory measured starlight using a newly calibrated spectrometer.",
                            BLOCK_QUESTION, "warmup")
    for repetition in range(5):
        names = list(engines) if repetition % 2 else list(reversed(engines))
        for case in cases:
            for name in names:
                start = time.perf_counter()
                response = await engines[name].decide(case["text"], BLOCK_QUESTION, "block")
                elapsed = (time.perf_counter() - start) * 1000
                answer = response["answers"]["content"] if response else None
                results[name].append({"id": case["id"], "label": case["label"], "response": response,
                                      "repetition": repetition, "correct": bool(answer and answer["choice"] == case["label"]),
                                      "accepted": bool(answer and answer["probabilities"][answer["choice"]] >= 0.9),
                                      "elapsed_ms": elapsed})
    summary = {"unique_cases": len(cases), "repetitions": 5,
               "corpus_sha256": hashlib.sha256(raw).hexdigest(), "models": {}}
    for name, rows in results.items():
        summary["models"][name] = {
            "accuracy": sum(r["correct"] for r in rows) / len(rows),
            "median_ms": statistics.median(r["elapsed_ms"] for r in rows),
            "min_ms": min(r["elapsed_ms"] for r in rows), "max_ms": max(r["elapsed_ms"] for r in rows),
            "accepted": sum(r["accepted"] for r in rows),
            "accepted_errors": sum(r["accepted"] and not r["correct"] for r in rows),
            "unique_failures": sorted({r["id"] for r in rows if not r["correct"]}),
        }
    output = Path("reports/blocks")
    output.mkdir(parents=True, exist_ok=True)
    (output / "raw.json").write_text(json.dumps(results, indent=2))
    (output / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
