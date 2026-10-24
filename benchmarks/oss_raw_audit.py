"""Audit raw versus selected Crawl4AI Markdown on the same recorded retrievals."""

import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

from benchmarks.oss_quality import score


def audit(phase, gold, output):
    if output.exists():
        raise FileExistsError(output)
    data = json.loads((phase / "summary.json").read_text())
    excluded = set(data["shared_cloudflare_exclusions"])
    values = defaultdict(list)
    for row in data["observations"]:
        if row["provider"] != "crawl4ai" or row["id"] in excluded or row["quality"] is None:
            continue
        folder = phase / f"repeat-{row['repetition']}" / row["id"] / "crawl4ai"
        result = json.loads((folder / "result.json").read_text())
        reference = json.loads((gold / (row["id"] + ".json")).read_text())
        pair = {}
        for name, field in [("selected", "text"), ("raw", "raw_markdown")]:
            quality = score(result.get(field, ""), reference)
            if row["outcome"] != "ok":
                quality["quality_pass"] = False
            pair[name] = quality
        assert abs(pair["selected"]["source_token_f1"] - row["quality"]["source_token_f1"]) < 1e-12
        values[row["id"]].append(pair)
    cases = {ident: {name: {
        "median_f1": median(v[name]["source_token_f1"] for v in group),
        "median_block_recall": median(v[name]["weighted_block_recall"] for v in group),
        "strict_pass_trials": sum(v[name]["quality_pass"] for v in group),
        "trials": len(group),
    } for name in ["selected", "raw"]} for ident, group in values.items()}
    result = {"source_phase": phase.name,
              "meaning": "Post-hoc output-choice sensitivity. Same retrievals/timings; choose raw for every scored case, never the better output per case.",
              "scored_sites": len(cases), "cases": cases,
              "aggregate": {name: {
                  "macro_site_median_f1": mean(v[name]["median_f1"] for v in cases.values()),
                  "macro_site_median_block_recall": mean(v[name]["median_block_recall"] for v in cases.values()),
                  "strict_pass_trials": sum(v[name]["strict_pass_trials"] for v in cases.values()),
                  "trials": sum(v[name]["trials"] for v in cases.values()),
              } for name in ["selected", "raw"]}}
    output.write_text(json.dumps(result, indent=2))
    print(json.dumps(result["aggregate"], indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit(args.phase, args.gold, args.output)
