import gzip
import hashlib
import json
from pathlib import Path

from optimal_crawler.urls import canonicalize, origin

from .real50 import runtime_digest
from .real50_review import reviewed_checks


def snapshot_digest(directory):
    digest = hashlib.sha256()
    for path in sorted(directory.glob("*.py")):
        digest.update(str(Path("src/optimal_crawler") / path.name).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def main():
    root = Path("reports/real50")
    cases = json.loads(Path("benchmarks/real50.json").read_text())
    urls = {canonicalize(case["url"]) for case in cases}
    origins = {origin(url) for url in urls}
    digest = hashlib.sha256(Path("benchmarks/real50.json").read_bytes()).hexdigest()
    comparison = json.loads((root / "comparison.json").read_text())
    expected_settings = None
    harness = None
    total_documents, total_requests, checked_bodies = 0, 0, 0
    for phase in ("baseline", "after"):
        directory = root / phase
        metadata = json.loads((directory / "phase.json").read_text())
        assert metadata["manifest_sha256"] == digest
        assert snapshot_digest(directory / "source") == metadata["runtime_sha256"]
        assert hashlib.sha256((directory / "manifest.json").read_bytes()).hexdigest() == digest
        assert hashlib.sha256((directory / "harness.py").read_bytes()).hexdigest() == metadata["harness_sha256"]
        if harness is None:
            harness = metadata["harness_sha256"]
        assert harness == metadata["harness_sha256"]
        if phase == "after":
            assert runtime_digest() == metadata["runtime_sha256"]
        seen_bodies = set()
        for strategy in ("http", "browser", "adaptive"):
            reviewed_counts = []
            for repetition in range(5):
                record = json.loads((directory / f"run-{repetition}-{strategy}.json").read_text())
                assert record["manifest_sha256"] == digest
                assert record["runtime_sha256"] == metadata["runtime_sha256"]
                assert record["harness_sha256"] == harness
                assert record["summary"]["status"] == "completed"
                assert record["summary"]["pages"] == len(record["documents"]) == 50
                assert {doc["url"] for doc in record["documents"]} == urls
                assert record["summary"]["requests"] == len(record["requests"])
                assert record["summary"]["bytes"] == sum(q["bytes"] for q in record["requests"])
                assert record["summary"]["decision_calls"] == 0
                assert all(origin(q["url"]) in origins for q in record["requests"])
                settings = {k: v for k, v in record["config"].items() if k not in {"db", "strategy"}}
                if expected_settings is None:
                    expected_settings = settings
                assert settings == expected_settings, "Strategy or phase limits changed"
                reviewed_counts.append(sum(c["passed"] for c in reviewed_checks(record)))
                total_documents += len(record["documents"])
                total_requests += len(record["requests"])
                for capture in record["captures"]:
                    body_hash = capture["body_sha256"]
                    if body_hash and body_hash not in seen_bodies:
                        data = gzip.decompress((directory / "bodies" / (body_hash + ".gz")).read_bytes())
                        assert hashlib.sha256(data).hexdigest() == body_hash
                        seen_bodies.add(body_hash)
                        checked_bodies += 1
            assert reviewed_counts == comparison[phase]["results"][strategy]["reviewed_passes"]
    assert total_documents == 1500
    assert "Task was destroyed" not in Path(".audit/real50-after.log").read_text()
    result = {"verified": True, "runs": 30, "documents": total_documents,
              "requests": total_requests, "unique_response_bodies_verified": checked_bodies,
              "equal_settings": True, "no_model_calls": True,
              "source_snapshots_verified": True, "no_pending_task_warnings_after": True}
    (root / "verification.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
