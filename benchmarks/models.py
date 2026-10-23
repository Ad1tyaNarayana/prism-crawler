import argparse
import asyncio
import hashlib
import json
import statistics
import time
from pathlib import Path

from optimal_crawler.decisions import ROUTE_QUESTION, DecisionEngine
from optimal_crawler.models import CrawlConfig


def parser_question(state):
    return {"parser": {"type": "choice", "instructions":
        "Which candidate contains the complete main article, with the least navigation or boilerplate?",
        "criteria": {key: f"Candidate {key}" for key in state}}}


def aggregate(records):
    valid = [r for r in records if r["answer"]]
    accepted = [r for r in valid if r["probability"] >= 0.9]
    latencies = [r["elapsed_ms"] for r in records]
    return {
        "calls": len(records), "valid": len(valid),
        "accuracy": sum(r["correct"] for r in valid) / max(1, len(records)),
        "route_accuracy": sum(r["correct"] for r in records if r["kind"] == "route") / max(1, sum(r["kind"] == "route" for r in records)),
        "parser_accuracy": sum(r["correct"] for r in records if r["kind"] == "parser") / max(1, sum(r["kind"] == "parser" for r in records)),
        "accepted_at_0_9": len(accepted), "accepted_errors": sum(not r["correct"] for r in accepted),
        "brier": statistics.mean(r["brier"] for r in valid) if valid else None,
        "median_ms": statistics.median(latencies), "min_ms": min(latencies), "max_ms": max(latencies),
        "unique_failures": sorted({r["id"] for r in records if not r["correct"]}),
    }


async def run(repetitions, output):
    raw = Path("benchmarks/decision_cases.json").read_bytes()
    cases = json.loads(raw)
    output.mkdir(parents=True, exist_ok=True)
    engines = {
        "laya-multilingual": DecisionEngine(CrawlConfig(decision_provider="laya")),
        "kev-0.8b": DecisionEngine(CrawlConfig(decision_provider="systemone",
                                              decision_endpoint="http://127.0.0.1:8019")),
    }
    warmup = {}
    for name, engine in engines.items():
        start = time.perf_counter()
        answer = await engine.decide("A complete article has already been extracted from this page.",
                                     ROUTE_QUESTION, "warmup")
        warmup[name] = {"elapsed_ms": (time.perf_counter() - start) * 1000, "response": answer}
    results = {name: [] for name in engines}
    for repetition in range(repetitions):
        names = list(engines) if repetition % 2 == 0 else list(reversed(engines))
        for case in cases:
            question = ROUTE_QUESTION if case["kind"] == "route" else parser_question(case["state"])
            key = "route" if case["kind"] == "route" else "parser"
            for name in names:
                start = time.perf_counter()
                response = await engines[name].decide(case["state"], question, case["kind"])
                elapsed = (time.perf_counter() - start) * 1000
                answer = response["answers"][key] if response else None
                probs = answer["probabilities"] if answer else {}
                results[name].append({"id": case["id"], "repetition": repetition,
                                      "kind": case["kind"], "label": case["label"],
                                      "answer": answer, "response": response,
                                      "correct": bool(answer and answer["choice"] == case["label"]),
                                      "probability": probs.get(answer["choice"], 0) if answer else 0,
                                      "brier": sum((p - int(option == case["label"])) ** 2 for option, p in probs.items()),
                                      "elapsed_ms": elapsed})
        print(json.dumps({"repetition": repetition, "results": {name: aggregate(rows) for name, rows in results.items()}}), flush=True)
        (output / "raw.json").write_text(json.dumps({"warmup": warmup, "records": results}, indent=2))
    summary = {"corpus_sha256": hashlib.sha256(raw).hexdigest(), "unique_cases": len(cases),
               "repetitions": repetitions, "threshold": 0.9, "warmup": warmup,
               "models": {name: aggregate(rows) for name, rows in results.items()},
               "jev": {"status": "not_run", "reason": "No API credential or paid inference used"}}
    (output / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--output", type=Path, default=Path("reports/models"))
    args = parser.parse_args()
    asyncio.run(run(args.repetitions, args.output))
