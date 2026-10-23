"""Review provider snapshots with the original checks; replay HTML without network I/O."""

import argparse
import hashlib
import json
from html import escape
from pathlib import Path

from lxml import html

from benchmarks.real50 import SiteCase, runtime_digest, score
from benchmarks.real50_review import reviewed_checks
from optimal_crawler.extract import Extractor, access_wall
from optimal_crawler.models import Candidate, FetchResult
from optimal_crawler.verification import ContentVerifier, normalize

ROOT = Path("reports/provider-check/cache-only")


def checks(case, text, final_url, outcome="ok"):
    document = {"url": case.url, "final_url": final_url, "outcome": outcome, "text": text}
    return reviewed_checks({"checks": score([case], [document]), "documents": [document]})[0]


def replay(case, data):
    metadata = data.get("metadata", {})
    raw = data.get("rawHtml", "")
    if not raw:
        return None
    result = FetchResult(metadata.get("url", case.url), metadata.get("statusCode", 200),
                         raw.encode(), {"content-type": metadata.get("contentType", "text/html")})
    extraction = Extractor().extract(result)
    for candidate in extraction.candidates:
        if candidate.title and normalize(candidate.title) not in normalize(candidate.text):
            candidate.text = candidate.title + "\n" + candidate.text
    ContentVerifier().apply(result, extraction, case.url)
    candidate = extraction.best
    accepted = bool(candidate and candidate.quality >= 0.5
                    and candidate.verification.accepted and not extraction.wall)
    text = candidate.text if candidate else ""
    verifier = ContentVerifier()
    provider = Candidate("firecrawl", data.get("markdown", ""), metadata.get("title", ""), 1)
    provider_assessment = verifier.assess(case.url, result, provider, verifier.source(result))
    return {"html_sha256": hashlib.sha256(result.body).hexdigest(),
            "provider_assessment": verifier.metadata(provider_assessment),
            "parser": candidate.parser if candidate else None,
            "title": candidate.title if candidate else "", "text": text,
            "accepted": accepted,
            "assessment": ContentVerifier().metadata(candidate.verification) if candidate else None,
            "check": checks(case, text, result.url, "ok" if accepted else "incomplete")}


def review():
    all_cases = [SiteCase(**c) for c in json.loads(Path("benchmarks/real50.json").read_text())]
    selected = json.loads((ROOT / "finished.json").read_text())["cases"]
    cases = [c for c in all_cases if c.id in selected]
    exa = json.loads((ROOT / "exa-batch15.body.json").read_text())
    exa_results = {r["url"]: r for r in exa.get("results", [])}
    exa_statuses = {r["id"]: r for r in exa.get("statuses", [])}
    rows = []
    for case in cases:
        fire = json.loads((ROOT / ("firecrawl-" + case.id + ".body.json")).read_text())
        data, status = fire.get("data", {}), exa_statuses.get(case.url, {})
        metadata, result = data.get("metadata", {}), exa_results.get(case.url, {})
        fire_text = data.get("markdown", "")
        exa_text = result.get("text", "")
        if result.get("title") and normalize(result["title"]) not in normalize(exa_text):
            exa_text = result["title"] + "\n" + exa_text
        fire_wall = bool(data.get("rawHtml") and access_wall(html.fromstring(data["rawHtml"])))
        exa_wall = bool(result.get("title") and access_wall(
            html.fromstring("<title>" + escape(result["title"]) + "</title>")))
        rows.append({
            "id": case.id, "url": case.url,
            "firecrawl": {"retrieved": bool(fire.get("success") and fire_text),
                          "cache_state": metadata.get("cacheState"),
                          "cached_at": metadata.get("cachedAt"),
                          "credits": metadata.get("creditsUsed"), "wall": fire_wall,
                          "check": checks(case, fire_text, metadata.get("url", case.url),
                                          "access_wall" if fire_wall else "ok"),
                          "replay": replay(case, data)},
            "exa": {"retrieved": status.get("status") == "success" and bool(exa_text),
                    "source": status.get("source"), "crawl_date": result.get("crawlDate"),
                    "error": status.get("error"), "wall": exa_wall,
                    "check": checks(case, exa_text, result.get("url", case.url),
                                    "access_wall" if exa_wall else "ok")},
        })
    before = json.loads((ROOT / "firecrawl-quota-before.body.json").read_text())["data"]
    after = json.loads((ROOT / "firecrawl-quota-after.body.json").read_text())["data"]
    return {"kind": "provider_cache_comparison_and_offline_parser_replay",
            "runtime_sha256": runtime_digest(), "cases": len(rows),
            "original_checks_modified": False, "target_live_fetches_requested": 0,
            "offline_replay_network_requests": 0,
            "firecrawl": {"retrieved": sum(r["firecrawl"]["retrieved"] for r in rows),
                          "check_passed": sum(r["firecrawl"]["check"]["passed"] for r in rows),
                          "source_verified_passed": sum(
                              r["firecrawl"]["check"]["passed"] and
                              not r["firecrawl"]["replay"]["provider_assessment"]["issues"]
                              for r in rows),
                          "credits_used": before["remainingCredits"] - after["remainingCredits"],
                          "credits_remaining": after["remainingCredits"]},
            "exa": {"retrieved": sum(r["exa"]["retrieved"] for r in rows),
                    "check_passed": sum(r["exa"]["check"]["passed"] for r in rows),
                    "cost_dollars_from_free_credits": exa.get("costDollars", {}).get("total")},
            "replay_check_passed": sum(bool(r["firecrawl"]["replay"] and
                                            r["firecrawl"]["replay"]["check"]["passed"]) for r in rows),
            "rows": rows}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    assert not args.output.exists(), "Preserve prior replay results"
    result = review()
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}, indent=2))
    print(json.dumps([{ "id": r["id"], "firecrawl": r["firecrawl"]["check"]["passed"],
                       "exa": r["exa"]["check"]["passed"],
                       "replay": r["firecrawl"]["replay"]["check"]["passed"]}
                      for r in result["rows"]], indent=2))
