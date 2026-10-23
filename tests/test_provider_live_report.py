import json

from benchmarks.provider_live_report import distribution, load_rows


def test_latency_summary_counts_failures_and_preserves_tail():
    result = distribution([10, 20, 30, 1000])
    assert result["n"] == 4
    assert result["median_ms"] == 25
    assert result["max_ms"] == 1000
    assert result["p95_ms"] > 800
    assert distribution([]) == {"n": 0}


def test_actual_freshness_labels_are_normalized_without_changing_receipts(tmp_path):
    folder = tmp_path / "run-0"
    folder.mkdir()
    base = {"id": "example", "repetition": 0, "attempted": True,
            "document": {"outcome": "ok", "text": "A substantive observatory report."},
            "check": {"passed": True}, "readable_check": {"passed": True}}
    exa = {**base, "provider": "exa", "freshness": "crawled",
           "receipt": {"elapsed_ms": 123, "request": {"maxAgeHours": 0}}}
    fire = {**base, "provider": "firecrawl", "freshness": "unknown",
            "receipt": {"elapsed_ms": 456, "request": {"maxAge": 0}}}
    for name, row in [("exa-example", exa), ("firecrawl-example", fire)]:
        (folder / (name + ".json")).write_text(json.dumps(row))
    (folder / "ours-events.json").write_text(json.dumps({"summary": {}, "requests": []}))
    rows = load_rows(tmp_path)
    assert len(rows) == 2
    assert all(r["quality_passed"] for r in rows)
    assert [r["duration_ms"] for r in rows] == [123, 456]
    assert json.loads((folder / "exa-example.json").read_text()) == exa


def test_explicit_cached_result_and_wrong_product_are_not_quality_passes(tmp_path):
    folder = tmp_path / "run-0"
    folder.mkdir()
    base = {"repetition": 0, "attempted": True, "check": {"passed": True},
            "readable_check": {"passed": True}, "document": {"outcome": "ok", "text": "BILLY bookcases category"}}
    exa = {**base, "id": "ikea", "provider": "exa", "freshness": "crawled",
           "receipt": {"elapsed_ms": 1, "request": {"maxAgeHours": 0}}}
    fire = {**base, "id": "example", "provider": "firecrawl", "freshness": "cached",
            "receipt": {"elapsed_ms": 2, "request": {"maxAge": 0}}}
    for name, row in [("exa-ikea", exa), ("firecrawl-example", fire)]:
        (folder / (name + ".json")).write_text(json.dumps(row))
    rows = load_rows(tmp_path)
    assert not any(r["quality_passed"] for r in rows)
    assert rows[0]["original_live_passed"]
    assert "requested_product_identity_unverified" in rows[0]["review_flags"]
    assert "freshness_not_established" in rows[1]["review_flags"]
