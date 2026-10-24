"""Lock source-reviewed reference blocks before comparative provider observations."""

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from benchmarks.oss_quality import tokens
from benchmarks.oss_reference import propose


def build(reference, annotations_path, output, include_leaf_text=False, sensitivity=False):
    annotations = json.loads(annotations_path.read_text())
    rows = []
    output.mkdir(exist_ok=False)
    for row in json.loads((reference / "summary.json").read_text()):
        ident = row["case"]["id"]
        entry = {"case": row["case"], "eligibility": row["eligibility"], "reason": row["reason"]}
        if row["eligibility"] == "review_pending":
            raw = (reference / ident / "reference.html").read_text()
            note = annotations[ident]
            gold = propose(raw, note.get("root_xpath"), note.get("exclude_xpaths", ()), include_leaf_text)
            gold.update(
                reviewed=True,
                review_note=note["notes"],
                review_method=("Post-hoc reference-construction sensitivity analysis; outputs already observed. "
                               if sensitivity else "Single model-assisted source-only review; no provider output used. ")
                              + "Not independent human labelling.",
                source_sha256=hashlib.sha256(raw.encode()).hexdigest(),
            )
            count = len(tokens("\n".join(b["text"] for b in gold["blocks"])))
            entry.update(
                eligibility="eligible" if count >= 30 else "no_reference_content",
                reason=""
                if count >= 30
                else "Reference has fewer than 30 text tokens; no primary quality score",
                reference_tokens=count,
                reference_chars=gold["reference_chars"],
                reference_blocks=len(gold["blocks"]),
            )
            (output / (ident + ".json")).write_text(json.dumps(gold, indent=2, ensure_ascii=False))
        rows.append(entry)
    (output / "index.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False))
    inventory = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.glob("*.json")}
    (output / "lock.json").write_text(
        json.dumps(
            {
                "locked_at": datetime.now(UTC).isoformat(),
                "files": inventory,
                "scorer_sha256": hashlib.sha256(
                    Path("benchmarks/oss_quality.py").read_bytes()
                ).hexdigest(),
                "reference_schema": "semantic-and-unwrapped-v2" if include_leaf_text else "semantic-blocks-v1",
                "sensitivity_analysis": sensitivity,
                "meaning": "Reference/metric changes after provider outputs require a separate labelled sensitivity analysis.",
            },
            indent=2,
        )
    )
    print(
        json.dumps(
            {
                key: sum(r["eligibility"] == key for r in rows)
                for key in {r["eligibility"] for r in rows}
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--include-leaf-text", action="store_true")
    parser.add_argument("--sensitivity", action="store_true")
    a = parser.parse_args()
    build(a.reference, a.annotations, a.output, a.include_leaf_text, a.sensitivity)
