import asyncio
import json
import statistics
import time
from pathlib import Path

from optimal_crawler.decisions import BLOCK_QUESTION, ROUTE_QUESTION, DecisionEngine
from optimal_crawler.models import CrawlConfig

from .models import aggregate, parser_question


async def main():
    engine = DecisionEngine(CrawlConfig(decision_provider="laya", laya_model="english"))
    await engine.decide("A complete article has already been extracted.", ROUTE_QUESTION, "warmup")
    broad = json.loads(Path("benchmarks/decision_cases.json").read_text())
    blocks = json.loads(Path("benchmarks/blocks.json").read_text())
    broad_rows, block_rows = [], []
    for repetition in range(5):
        for case in broad:
            questions = ROUTE_QUESTION if case["kind"] == "route" else parser_question(case["state"])
            key = "route" if case["kind"] == "route" else "parser"
            start = time.perf_counter()
            response = await engine.decide(case["state"], questions, case["kind"])
            elapsed = (time.perf_counter() - start) * 1000
            answer = response["answers"][key] if response else None
            probs = answer["probabilities"] if answer else {}
            broad_rows.append({"id": case["id"], "repetition": repetition, "kind": case["kind"],
                               "label": case["label"], "answer": answer, "response": response,
                               "correct": bool(answer and answer["choice"] == case["label"]),
                               "probability": probs.get(answer["choice"], 0) if answer else 0,
                               "brier": sum((p - int(option == case["label"])) ** 2 for option, p in probs.items()),
                               "elapsed_ms": elapsed})
        for case in blocks:
            start = time.perf_counter()
            response = await engine.decide(case["text"], BLOCK_QUESTION, "block")
            elapsed = (time.perf_counter() - start) * 1000
            answer = response["answers"]["content"] if response else None
            block_rows.append({"id": case["id"], "response": response, "repetition": repetition,
                               "correct": bool(answer and answer["choice"] == case["label"]),
                               "accepted": bool(answer and answer["probabilities"][answer["choice"]] >= 0.9),
                               "elapsed_ms": elapsed})
    result = {"model": "laya-english", "repetitions": 5, "broad": aggregate(broad_rows),
              "blocks": {"accuracy": sum(r["correct"] for r in block_rows) / len(block_rows),
                         "median_ms": statistics.median(r["elapsed_ms"] for r in block_rows),
                         "accepted": sum(r["accepted"] for r in block_rows),
                         "accepted_errors": sum(r["accepted"] and not r["correct"] for r in block_rows)}}
    output = Path("reports/english")
    output.mkdir(parents=True, exist_ok=True)
    (output / "summary.json").write_text(json.dumps(result, indent=2))
    (output / "raw.json").write_text(json.dumps({"broad": broad_rows, "blocks": block_rows}, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
