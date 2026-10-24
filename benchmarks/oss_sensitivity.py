"""Re-score existing outputs under explicitly post-hoc source labels; no retrieval."""

import argparse
import hashlib
import json
from pathlib import Path

from benchmarks.oss_quality import score


def rescore(phase, gold, output):
    data = json.loads((phase / "summary.json").read_text())
    lock = json.loads((gold / "lock.json").read_text())
    assert lock["sensitivity_analysis"], "Must explicitly declare post-hoc labels"
    assert lock["scorer_sha256"] == hashlib.sha256(Path("benchmarks/oss_quality.py").read_bytes()).hexdigest()
    assert all(hashlib.sha256((gold / name).read_bytes()).hexdigest() == digest
               for name, digest in lock["files"].items())
    index = {r["case"]["id"]: r for r in json.loads((gold / "index.json").read_text())}
    rows = []
    for original in data["observations"]:
        row = dict(original)
        item = index[row["id"]]
        row["quality"] = None
        if item["eligibility"] == "eligible" and row["id"] not in data["shared_cloudflare_exclusions"]:
            path = phase / f"repeat-{row['repetition']}" / row["id"] / row["provider"] / "result.json"
            result = json.loads(path.read_text()) if path.exists() else {"text": ""}
            label = json.loads((gold / (row["id"] + ".json")).read_text())
            quality = score(result["text"], label)
            if row["outcome"] != "ok":
                quality["quality_pass"] = False
            row["quality"] = quality
        rows.append(row)
    output.mkdir(parents=True, exist_ok=False)
    provenance = {"source_phase": str(phase.resolve()), "gold_lock": lock,
                  "meaning": "Post-hoc reference sensitivity analysis of the same saved outputs. No new retrieval, no new timing measurements."}
    (output / "phase.json").write_text(json.dumps(provenance, indent=2))
    (output / "summary.json").write_text(json.dumps({**data, "observations": rows, **provenance}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rescore(args.phase, args.gold, args.output)
