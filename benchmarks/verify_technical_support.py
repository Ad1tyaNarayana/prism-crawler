"""Verify CLI and local-fixture gates independently of the live comparison scores."""

import json
from pathlib import Path

from .real50 import runtime_digest, write_json


def main():
    root = Path("reports/technical-final")
    summary = json.loads((root / "fixture/summary.json").read_text())
    for strategy, content in (("http", 15), ("browser", 18), ("adaptive", 18)):
        result = summary["results"][strategy]
        assert result["content_passed"] == [content]
        assert result["policy_passed"] == [4]
        assert result["receipt_match"] and result["forbidden_requests"] == 0
        run = json.loads((root / "fixture" / f"run-0-{strategy}.json").read_text())
        assert run["summary"]["status"] == "completed" and run["summary"]["pages"] == 22
        assert run["config"]["browser_timeout"] == 90
    cli = json.loads((root / "cli-verification.json").read_text())
    assert cli["verified"] and len(cli["documents"]) == 4
    assert "63 passed" in Path(".audit/final-full-tests.log").read_text()
    assert "All checks passed!" in Path(".audit/final-full-lint.log").read_text()
    for name in ("final-fixture.log", "final-cli.log", "final-full-tests.log"):
        text = (Path(".audit") / name).read_text()
        assert not any(item in text for item in ("Task was destroyed", "Task exception was never retrieved", "Traceback"))
    write_json(root / "support-verification.json", {
        "verified": True, "runtime_sha256": runtime_digest(), "unit_tests": 63,
        "fixture_content": {key: value["content_passed"][0] for key, value in summary["results"].items()},
        "fixture_policy_checks": 4, "forbidden_origin_receipts": 0,
        "fixture_uses_default_browser_budget": True, "actual_cli_documents": cli["documents"],
        "failed_earlier_checks_preserved": ["reports/technical/fixture/summary.json", "data/technical-demo.jsonl"],
    })
    print((root / "support-verification.json").read_text())


if __name__ == "__main__":
    main()
