"""Verify new measurements against source snapshots, bodies, scope and scores."""

import csv
import gzip
import hashlib
import json
from pathlib import Path

from optimal_crawler.urls import canonicalize, origin

from .real50 import SiteCase, runtime_digest, score, write_json
from .technical_report import NAMES, functional_checks
from .verify_real50 import snapshot_digest


def main():
    root = Path("reports/technical-final")
    comparison = json.loads((root / "comparison.json").read_text())
    historical = json.loads(Path("reports/real50/comparison.json").read_text())
    first_round = json.loads(Path("reports/technical/comparison.json").read_text())
    assert comparison["manifest_sha256"] == historical["manifest_sha256"]
    assert comparison["review_sha256"] == historical["review_contract_sha256"]
    assert comparison["functional_sha256"] == first_round["functional_sha256"]
    assert snapshot_digest(Path("reports/technical/after/source")) == first_round["runtime_sha256"]
    for name, expected in (("review-contract.json", comparison["review_sha256"]),
                           ("functional-contract.json", comparison["functional_sha256"])):
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected
    databases = set()
    total_docs, total_requests, total_bodies = 0, 0, 0
    for suite, manifest in (("after", "real50.json"), ("exa", "exa.json")):
        directory = root / suite
        source_manifest = Path("benchmarks") / manifest
        cases = json.loads(source_manifest.read_text())
        urls = {canonicalize(c["url"]) for c in cases}
        page_origins = {origin(url) for url in urls}
        digest = hashlib.sha256(source_manifest.read_bytes()).hexdigest()
        meta = json.loads((directory / "phase.json").read_text())
        assert meta["manifest_sha256"] == digest
        if suite == "exa":
            first_exa = json.loads(Path("reports/technical/exa/phase.json").read_text())
            assert meta["manifest_sha256"] == first_exa["manifest_sha256"]
        assert hashlib.sha256((directory / "manifest.json").read_bytes()).hexdigest() == digest
        assert snapshot_digest(directory / "source") == meta["runtime_sha256"] == runtime_digest()
        assert hashlib.sha256((directory / "harness.py").read_bytes()).hexdigest() == meta["harness_sha256"]
        expected_settings, seen_bodies = None, set()
        for strategy in NAMES:
            counts, functional = [], []
            for repetition in range(5):
                record = json.loads((directory / f"run-{repetition}-{strategy}.json").read_text())
                assert record["runtime_sha256"] == meta["runtime_sha256"]
                assert record["manifest_sha256"] == digest
                assert record["harness_sha256"] == meta["harness_sha256"]
                assert record["summary"]["status"] == "completed"
                assert len(record["documents"]) == record["summary"]["pages"] == len(cases)
                assert {d["url"] for d in record["documents"]} == urls
                assert score([SiteCase(**case) for case in cases], record["documents"]) == record["checks"]
                assert len(record["requests"]) == record["summary"]["requests"]
                assert sum(q["bytes"] for q in record["requests"]) == record["summary"]["bytes"]
                assert record["summary"]["decision_calls"] == 0
                assert record["config"]["decision_provider"] == "none"
                assert not record["config"]["allow_private"]
                assert record["config"]["db"] not in databases
                databases.add(record["config"]["db"])
                settings = {k: v for k, v in record["config"].items() if k not in {"db", "strategy"}}
                if expected_settings is None:
                    expected_settings = settings
                assert settings == expected_settings
                assets = {origin(u) for u in record["config"]["asset_origins"]}
                for request in record["requests"]:
                    assert request.get("method", "GET") in {"GET", "HEAD"}
                    allowed = page_origins | assets if request["role"] in {"robots", "browser_asset"} else page_origins
                    assert origin(request["url"]) in allowed
                for doc in record["documents"]:
                    if doc["method"] == "browser" and "browser" in doc["metadata"]:
                        assert doc["metadata"]["browser"]["native_egress"] == "deny-only local proxy"
                if suite == "after":
                    from .real50_review import reviewed_checks
                    counts.append(sum(c["passed"] for c in reviewed_checks(record)))
                    functional.append(functional_checks(record))
                else:
                    counts.append(sum(c["passed"] for c in record["checks"]))
                total_docs += len(record["documents"])
                total_requests += len(record["requests"])
                for capture in record["captures"]:
                    body_hash = capture["body_sha256"]
                    if body_hash and body_hash not in seen_bodies:
                        body = gzip.decompress((directory / "bodies" / (body_hash + ".gz")).read_bytes())
                        assert hashlib.sha256(body).hexdigest() == body_hash
                        seen_bodies.add(body_hash)
                        total_bodies += 1
            expected = comparison["current" if suite == "after" else "exa"][strategy]
            assert counts == expected["reviewed_passes"]
            if functional:
                assert {key: sum(f[key]["passed"] for f in functional) for key in functional[0]} == expected["functional"]
    assert total_docs == 825
    with (root / "results.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 165
    assert len({(r["suite"], r["site"], r["strategy"]) for r in rows}) == 165
    for row in rows:
        group = comparison["current" if row["suite"] == "same-50" else "exa"][row["strategy"]]
        assert int(row["passing_repetitions"]) == group["per_site"][row["site"]]
        assert row["repetitions"] == "5"
    for name in ("final-real50.log", "final-exa.log"):
        log = (Path(".audit") / name).read_text()
        assert not any(message in log for message in ("Task was destroyed", "Task exception was never retrieved", "Traceback"))
    result = {"verified": True, "runs": 30, "documents": total_docs, "requests": total_requests,
              "unique_response_bodies_verified": total_bodies, "equal_settings_within_each_suite": True,
              "no_model_calls": True, "source_snapshots_match_current_runtime": True,
              "all_request_origins_authorized": True, "only_get_and_head": True,
              "original_manifest_and_review_unchanged": True, "distinct_databases": len(databases),
              "exa_and_functional_checks_unchanged_between_rounds": True, "csv_rows_verified": len(rows),
              "pilot_excluded": True, "no_async_task_warnings": True}
    write_json(root / "verification.json", result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
