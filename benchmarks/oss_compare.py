"""Run frozen, interleaved, live local-library trials. Never reuse response caches."""

import argparse
import asyncio
import hashlib
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from benchmarks.oss_quality import score
from benchmarks.oss_trial import PYTHONS, REPO, trial

PROVIDERS = ["prism", "crawlee", "crawl4ai"]


def digest(paths):
    return {str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str))


async def run(args):
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    index = json.loads((args.gold / "index.json").read_text())
    if args.case_ids:
        index = [c for c in index if c["case"]["id"] in args.case_ids]
    source_files = sorted((REPO / "src").rglob("*.py"))
    harness = [
        REPO / "benchmarks" / ("oss_" + name + ".py")
        for name in ["policy", "worker", "quality", "trial"]
    ]
    frozen = digest(source_files + harness)
    lock = json.loads((args.gold / "lock.json").read_text())
    assert all(
        hashlib.sha256((args.gold / n).read_bytes()).hexdigest() == h
        for n, h in lock["files"].items()
    )
    assert frozen["benchmarks/oss_quality.py"] == lock["scorer_sha256"]
    save(
        root / "phase.json",
        {
            "started_at": datetime.now(UTC).isoformat(),
            "source_hashes": frozen,
            "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
            "gold_lock": lock,
            "repetitions": args.repetitions,
            "providers": args.providers,
            "cache": "fresh subprocess, database, storage, browser context for each observation",
            "configuration": "cold one-URL jobs, honest shared UA, no stealth/proxies/auth, strict common benchmark policy for competitors; Prism production guard",
            "case_ids": [c["case"]["id"] for c in index],
        },
    )
    shutil.copytree(REPO / "src", root / "source", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(
        REPO / "benchmarks", root / "harness", ignore=shutil.ignore_patterns("__pycache__")
    )
    shutil.copytree(args.gold, root / "gold")
    for provider in args.providers:
        (root / (provider + "-requirements.txt")).write_text(
            subprocess.check_output([str(PYTHONS[provider]), "-m", "pip", "freeze"], text=True)
        )
    results = []
    cloudflare = {c["case"]["id"] for c in index if c["eligibility"] == "cloudflare_skip"}
    for repetition in range(args.repetitions):
        for position, item in enumerate(index):
            case = item["case"]
            ident = case["id"]
            order = (
                PROVIDERS[(position + repetition) % 3 :] + PROVIDERS[: (position + repetition) % 3]
            )
            for provider in [p for p in order if p in args.providers]:
                assert digest(source_files + harness) == frozen, (
                    "Source/adapter changed during observation phase"
                )
                folder = root / f"repeat-{repetition}" / ident / provider
                if ident in cloudflare:
                    result = {
                        "provider": provider,
                        "outcome": "cloudflare_skip",
                        "reason": "Shared confirmed-Cloudflare exclusion",
                        "end_to_end_s": None,
                        "browser_pages": 0,
                        "request_count": 0,
                        "text": "",
                    }
                else:
                    result = await trial(provider, case, folder)
                    if result["outcome"] == "cloudflare_skip":
                        cloudflare.add(ident)
                if item["eligibility"] == "eligible" and ident not in cloudflare:
                    gold = json.loads((args.gold / (ident + ".json")).read_text())
                    quality = score(result["text"], gold)
                    if result.get("outcome") != "ok":
                        quality["quality_pass"] = False
                    if result.get("raw_markdown") is not None:
                        result["raw_markdown_quality"] = score(result["raw_markdown"], gold)
                else:
                    quality = None
                row = {
                    "id": ident,
                    "category": case["category"],
                    "cohort": case["cohort"],
                    "repetition": repetition,
                    "provider": provider,
                    "outcome": result["outcome"],
                    "reference_eligibility": item["eligibility"],
                    "quality": quality,
                    "chars": len(result["text"]),
                    **{
                        k: result.get(k)
                        for k in [
                            "reason",
                            "end_to_end_s",
                            "library_elapsed_s",
                            "browser_pages",
                            "browser_launches",
                            "browser_launch_ms",
                            "request_count",
                            "peak_sampled_rss_bytes",
                            "exit_code",
                        ]
                    },
                }
                results.append(row)
                save(root / "progress.json", results)
                if folder.exists():
                    save(folder / "scored.json", {**result, "quality": quality})
                print(json.dumps(row, ensure_ascii=False), flush=True)
    save(
        root / "summary.json",
        {
            "finished_at": datetime.now(UTC).isoformat(),
            "observations": results,
            "shared_cloudflare_exclusions": sorted(cloudflare),
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--providers", nargs="+", choices=PROVIDERS, default=PROVIDERS)
    parser.add_argument("--case-ids", nargs="+")
    asyncio.run(run(parser.parse_args()))
