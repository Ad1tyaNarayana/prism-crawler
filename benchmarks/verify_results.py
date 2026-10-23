import json
from pathlib import Path

from .run import source_digest


def main():
    directory = Path("reports/benchmark")
    summary = json.loads((directory / "summary.json").read_text())
    assert summary["source_sha256"] == source_digest(), "Source changed after the reported benchmark"
    for strategy, aggregate in summary["results"].items():
        assert aggregate["runs"] == 5
        assert aggregate["receipt_match"]
        assert aggregate["forbidden_requests"] == 0
        for repetition in range(5):
            run = json.loads((directory / f"run-{repetition}-{strategy}.json").read_text())
            assert len(run["requests"]) == len(run["receipts"]) == run["summary"]["requests"]
            assert run["score"]["policy_passed"] == run["score"]["policy_total"] == 4
            assert not any(receipt["path"] == "/private/secret" for receipt in run["receipts"])
            if strategy == "adaptive":
                assert run["score"]["content_passed"] == run["score"]["content_total"] == 18
    live = json.loads(Path("reports/live/adaptive.json").read_text())
    assert live["summary"]["content_checks_passed"] == 6
    assert all(check["passed"] for check in live["content_checks"])
    example = json.loads(Path("data/demo.jsonl").read_text())
    assert example["outcome"] == "ok" and "Albert Einstein" in example["text"]
    assert example["method"] == "browser"
    print("Verified all 15 measured runs, origin receipts, six live content checks, and the real CLI example.")


if __name__ == "__main__":
    main()
