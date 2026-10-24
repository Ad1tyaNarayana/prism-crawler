import json

import pytest

from benchmarks.fixture import site
from benchmarks.oss_policy import Policy, cloudflare
from benchmarks.oss_quality import score, tokens


def gold():
    return {
        "blocks": [
            {
                "text": "First measurement establishes a calibrated source record with its complete original details."
            },
            {
                "text": "Second observation follows the research method and retains the final evidence marker."
            },
        ],
        "headings": [],
        "code": [],
        "boilerplate": [
            "Subscribe to our weekly marketing newsletter and receive every promotional offer by email."
        ],
    }


def test_source_score_requires_content_and_penalizes_extra_noise():
    reference = gold()
    complete = "\n".join(b["text"] for b in reference["blocks"])
    assert score(complete, reference)["quality_pass"]
    assert score(complete, reference)["source_token_f1"] == 1
    assert not score(reference["blocks"][0]["text"], reference)["quality_pass"]
    dirty = complete + "\n" + reference["boilerplate"][0] * 4
    assert not score(dirty, reference)["quality_pass"]
    assert score(dirty, reference)["matched_boilerplate_token_ratio"] > 0


def test_cjk_reference_and_cloudflare_detection_are_not_keyword_successes():
    assert len(tokens("これは研究の記録です。")) >= 10
    assert cloudflare(403, {"cf-mitigated": "challenge"})
    assert not cloudflare(200, {"server": "cloudflare"}, "<main>Ordinary public content</main>")


async def test_benchmark_policy_never_sends_a_robots_denied_request(tmp_path):
    async with site() as fixture:
        async with Policy({"url": fixture.base}, tmp_path, private=True) as policy:
            allowed = await policy.fetch(fixture.base + "/static")
            refused = await policy.fetch(fixture.base + "/private/secret")
            assert allowed["status"] == 200
            assert refused["reason"] == "robots_denied"
            assert fixture.counts["/private/secret"] == 0
    assert (
        json.loads((tmp_path / "policy.json").read_text())["denied"][0]["reason"] == "robots_denied"
    )


def test_source_reference_keeps_content_cards_inside_navigation():
    pytest.importorskip("html5lib")
    from benchmarks.oss_reference import propose

    raw = (
        "<main><h1>About our institution</h1><nav><article><h2>Our research programme</h2>"
        "<p>Scientists publish their original findings and open measurement records.</p></article></nav></main>"
    )
    reference = propose(raw)
    assert "Our research programme" in reference["headings"]
    assert any("Scientists publish" in b["text"] for b in reference["blocks"])


def test_reference_v2_includes_unwrapped_descriptions_without_duplicate_blocks():
    pytest.importorskip("html5lib")
    from benchmarks.oss_reference import propose

    raw = ('<main><h1>Research institute</h1><section><h2>Open collection</h2>'
           '<div>Independent measurement descriptions are available for public review.</div>'
           '<p>Published <strong>research records</strong> retain their provenance.</p>'
           '</section></main>')
    original = propose(raw)
    revised = propose(raw, include_leaf_text=True)
    assert not any("measurement descriptions" in b["text"] for b in original["blocks"])
    assert sum("measurement descriptions" in b["text"] for b in revised["blocks"]) == 1
    assert sum("research records" in b["text"] for b in revised["blocks"]) == 1
    table = propose('<main><h1>Downloads</h1><table><tr><th>Platform</th><th>Release</th></tr>'
                    '<tr><td>Windows</td><td>Stable</td></tr></table></main>', include_leaf_text=True)
    assert {"Platform", "Release", "Windows", "Stable"} <= {b["text"] for b in table["blocks"]}


def test_report_excludes_every_providers_earlier_results_after_a_shared_challenge(tmp_path):
    from benchmarks.oss_report import summarize

    reference = gold()
    good = score("\n".join(b["text"] for b in reference["blocks"]), reference)
    rows = []
    for ident in ["ordinary", "challenged"]:
        for provider in ["prism", "crawlee", "crawl4ai"]:
            rows.append({"id": ident, "category": "fixture", "cohort": "development",
                         "provider": provider, "repetition": 0, "reference_eligibility": "eligible",
                         "quality": good, "outcome": "ok", "chars": 200,
                         "end_to_end_s": 1 if ident == "ordinary" else 999,
                         "browser_pages": 0})
    (tmp_path / "summary.json").write_text(json.dumps({"finished_at": "fixture",
        "observations": rows, "shared_cloudflare_exclusions": ["challenged"]}))
    report = summarize(tmp_path)
    for result in report["providers"].values():
        assert result["scored_sites"] == 1
        assert result["scored_trials"] == 1
        assert result["median_site_median_s"] == 1
        assert result["non_cloudflare_trials"] == 1
