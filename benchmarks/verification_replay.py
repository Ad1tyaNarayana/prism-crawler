"""Offline acceptance checks on retained responses; makes no network requests."""
import argparse
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path

from optimal_crawler.extract import Extractor
from optimal_crawler.models import Candidate, FetchResult
from optimal_crawler.verification import ContentVerifier


def replay(paths: list[Path]) -> dict:
    extractor, verifier = Extractor(), ContentVerifier()
    rows, skipped = [], []
    for path in paths:
        run = json.loads(path.read_text())
        checks = {check["url"]: check for check in run["checks"]}
        for document in run["documents"]:
            if document["outcome"] != "ok":
                continue
            role = "rendered_dom" if document["method"] == "browser" else document["method"]
            captures = [c for c in run["captures"] if c["role"] == role
                        and c["url"] == document["final_url"] and c.get("body_sha256")]
            if not captures:
                skipped.append({"run": str(path), "url": document["url"], "reason": "No matching capture"})
                continue
            capture = captures[-1]
            body = gzip.decompress((path.parent / "bodies" / (capture["body_sha256"] + ".gz")).read_bytes())
            result = FetchResult(document["final_url"], capture["status"], body,
                                 capture.get("headers", {"content-type": "text/html"}))
            extraction = extractor.extract(result)
            verifier.apply(result, extraction, document["url"])
            previous = Candidate(document["parser"], document["text"], document["title"], document["quality"])
            previous.verification = verifier.assess(document["url"], result, previous, extraction.evidence)
            candidate = extraction.best
            accepted = bool(candidate and candidate.quality >= 0.5 and candidate.verification.accepted
                            and not extraction.wall)
            rows.append({"run": str(path), "url": document["url"], "strategy": run["strategy"],
                         "body_sha256": capture["body_sha256"],
                         "historical_check_passed": checks[document["url"]]["passed"],
                         "historical_chars": len(document["text"]),
                         "historical_text_assessment": verifier.metadata(previous.verification),
                         "new_parser": candidate.parser if candidate else None,
                         "new_chars": len(candidate.text) if candidate else 0, "new_accepted": accepted,
                         "new_assessment": verifier.metadata(candidate.verification) if candidate else None})
    source_hash = hashlib.sha256()
    for source in sorted(Path("src/optimal_crawler").glob("*.py")):
        source_hash.update(source.name.encode())
        source_hash.update(source.read_bytes())
    return {"kind": "offline_response_replay", "network_requests": 0,
            "limits": "Re-extraction and acceptance only; no live coverage, routing recovery or latency measurement.",
            "runtime_sha256": source_hash.hexdigest(), "inputs": [str(p) for p in paths],
            "records": len(rows), "skipped": skipped,
            "new_accepted": sum(r["new_accepted"] for r in rows),
            "new_rejected": sum(not r["new_accepted"] for r in rows),
            "issues": dict(Counter(issue for r in rows for issue in
                                   (r["new_assessment"] or {}).get("issues", []))),
            "rows": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = replay(args.runs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key != "rows"}, indent=2))


if __name__ == "__main__":
    main()
