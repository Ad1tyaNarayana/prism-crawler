from types import SimpleNamespace

import pytest

from benchmarks.provider_live import BenchmarkNetwork, provider_document, quality_review
from benchmarks.real50 import SiteCase
from optimal_crawler.models import CrawlConfig, Outcome

CASE = SiteCase("example", "fixture", "https://example.com/page", 25, [["observatory"]])
TEXT = "The observatory publishes calibrated measurements from its research instruments."


@pytest.mark.parametrize("source", ["live", "cached"])
def test_exa_live_request_retains_actual_freshness(source):
    payload = {"results": [{"url": CASE.url, "title": "Observatory report", "text": TEXT}],
               "statuses": [{"id": CASE.url, "status": "success", "source": source}],
               "costDollars": {"total": 0.001}}
    document, freshness, raw, charge = provider_document("exa", CASE, 200, payload)
    assert document["outcome"] == "ok"
    assert freshness == source
    assert raw is None and charge == 0.001
    assert quality_review(CASE, document)["check"]["passed"]


def test_provider_success_containing_challenge_is_a_failure():
    payload = {"results": [{"url": CASE.url, "title": "Client Challenge", "text": TEXT}],
               "statuses": [{"id": CASE.url, "status": "success", "source": "live"}]}
    document, _, _, _ = provider_document("exa", CASE, 200, payload)
    assert document["outcome"] == "access_wall"
    assert not quality_review(CASE, document)["check"]["passed"]


def test_firecrawl_cache_hit_is_not_relabelled_as_live():
    payload = {"success": True, "data": {"markdown": TEXT,
               "metadata": {"url": CASE.url, "cacheState": "hit", "creditsUsed": 1}}}
    document, freshness, _, charge = provider_document("firecrawl", CASE, 200, payload)
    assert freshness == "cached" and charge == 1
    assert document["outcome"] == "ok"


def test_readable_check_does_not_count_keywords_only_in_urls():
    text = "A published research report is available through this external reference. " + \
           "[Read more](https://example.com/observatory)"
    document = {"url": CASE.url, "final_url": CASE.url, "outcome": "ok", "text": text}
    review = quality_review(CASE, document)
    assert review["check"]["passed"]
    assert not review["readable_check"]["passed"]


async def test_benchmark_policy_override_preserves_scope_and_private_ip_guards():
    store = SimpleNamespace(events=lambda *_: [])
    network = BenchmarkNetwork(CrawlConfig(), store, "fixture", {"https://example.com"})
    assert await network.allowed(CASE.url) == (Outcome.OK, "")
    assert (await network.allowed("https://other.example/page"))[0] == Outcome.OUT_OF_SCOPE
    assert (await network.allowed("http://127.0.0.1/private"))[0] == Outcome.OUT_OF_SCOPE
