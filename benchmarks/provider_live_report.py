"""Analyze immutable live benchmark receipts without making network requests."""

import argparse
import csv
import gzip
import hashlib
import json
import math
import statistics
from collections import Counter
from html import escape
from pathlib import Path

PROVIDERS = ("exa", "firecrawl", "ours")


def distribution(values):
    if not values:
        return {"n": 0}
    ordered = sorted(values)

    def percentile(p):
        position = (len(ordered) - 1) * p
        low, high = math.floor(position), math.ceil(position)
        return ordered[low] + (ordered[high] - ordered[low]) * (position - low)

    return {"n": len(values), "median_ms": statistics.median(values), "p90_ms": percentile(.9),
            "p95_ms": percentile(.95), "min_ms": min(values), "max_ms": max(values),
            "mean_ms": statistics.mean(values), "sum_ms": sum(values)}


def load_rows(root):
    rows = []
    for provider in PROVIDERS:
        for path in sorted(root.glob(f"run-*/{provider}-*.json")):
            row = json.loads(path.read_text())
            if row.get("provider") != provider or "id" not in row or "repetition" not in row:
                continue
            row["artifact"] = str(path.relative_to(root))
            row["duration_ms"] = row.get("receipt", {}).get("elapsed_ms", row.get("elapsed_ms"))
            if not row.get("attempted"):
                row.update(live_eligible=False, quality_passed=False, review_flags=["not_attempted"])
                rows.append(row)
                continue
            receipt = row.get("receipt", {})
            if provider == "ours":
                evidence = "fresh_database_no_conditional_request"
                eligible = True
            elif provider == "exa":
                evidence = "response_source_" + row.get("freshness", "unknown")
                eligible = row.get("freshness") in {"live", "crawled"}
            else:
                request = receipt.get("request") or {}
                eligible = request.get("maxAge") == 0 and row.get("freshness") != "cached"
                evidence = "api_contract_maxAge_zero" if eligible else "cached_or_unconfirmed"
            flags = []
            if not eligible:
                flags.append("freshness_not_established")
            if not row.get("readable_check", {}).get("passed"):
                flags.append("readable_content_check_failed")
            document = row.get("document", {})
            if document.get("outcome") == "ok" and document.get("status", 200) >= 400:
                flags.append("non_success_target_status")
            if row["id"] == "ikea" and document.get("outcome") == "ok":
                digits = "".join(c for c in document.get("text", "") if c.isdigit())
                if "00263850" not in digits:
                    flags.append("requested_product_identity_unverified")
            row.update(freshness_evidence=evidence, live_eligible=eligible, review_flags=flags,
                       original_live_passed=eligible and row.get("check", {}).get("passed", False),
                       quality_passed=eligible and row.get("check", {}).get("passed", False) and not flags)
            rows.append(row)
    return rows


def reason(row):
    if not row.get("attempted"):
        return "not_attempted"
    if row.get("quality_passed"):
        return "passed"
    if "requested_product_identity_unverified" in row.get("review_flags", []):
        return "product_identity_unverified"
    document = row.get("document", {})
    if document.get("outcome") != "ok":
        return document.get("outcome", "unknown")
    if not row.get("live_eligible"):
        return "freshness_unconfirmed"
    return "content_check_failed"


def analyze(root):
    phase = json.loads((root / "phase.json").read_text())
    cases = json.loads((root / "manifest.json").read_text())
    rows = load_rows(root)
    by_key = {(r["provider"], r["id"], r["repetition"]): r for r in rows}
    matched = {(case["id"], rep) for case in cases for rep in range(phase["repetitions"])
               if all(by_key.get((provider, case["id"], rep), {}).get("quality_passed")
                      for provider in PROVIDERS)}
    providers = {}
    for provider in PROVIDERS:
        group = [r for r in rows if r["provider"] == provider]
        attempted = [r for r in group if r.get("attempted")]
        successful = [r for r in attempted if r["quality_passed"]]
        counts = [sum(r.get("original_live_passed", False) for r in group if r["repetition"] == rep)
                  for rep in range(phase["repetitions"])]
        reviewed = [sum(r.get("quality_passed", False) for r in group if r["repetition"] == rep)
                    for rep in range(phase["repetitions"])]
        providers[provider] = {
            "records": len(group), "attempted": len(attempted),
            "original_live_passed_by_round": counts, "quality_passed_by_round": reviewed,
            "original_passes": sum(counts), "quality_passes": sum(reviewed),
            "api_or_crawler_ok": sum(r.get("document", {}).get("outcome") == "ok" for r in attempted),
            "all_latency": distribution([r["duration_ms"] for r in attempted]),
            "passed_latency": distribution([r["duration_ms"] for r in successful]),
            "failed_latency": distribution([r["duration_ms"] for r in attempted if not r["quality_passed"]]),
            "matched_success_latency": distribution([r["duration_ms"] for r in attempted
                                                       if (r["id"], r["repetition"]) in matched]),
            "failure_counts": dict(Counter(reason(r) for r in group if not r.get("quality_passed"))),
            "freshness_evidence": dict(Counter(r.get("freshness_evidence", "not_attempted") for r in group)),
            "reported_charge": sum(r.get("charge", 0) for r in attempted),
            "passed_text_characters": {"unit": "characters", **{
                k.removesuffix("_ms"): v for k, v in
                distribution([r["characters"] for r in successful]).items()}},
            "category": {category: {"passes": sum(r.get("quality_passed", False) for r in group
                                                     if next(c["category"] for c in cases if c["id"] == r["id"]) == category),
                                      "total": sum(1 for r in group if next(c["category"] for c in cases
                                                                            if c["id"] == r["id"]) == category)}
                         for category in sorted({c["category"] for c in cases})},
        }
    site_rows = []
    for case in cases:
        per_provider = {}
        for provider in PROVIDERS:
            group = [r for r in rows if r["provider"] == provider and r["id"] == case["id"]]
            per_provider[provider] = {
                "quality_passes": sum(r.get("quality_passed", False) for r in group),
                "original_passes": sum(r.get("original_live_passed", False) for r in group),
                "attempted": sum(r.get("attempted", False) for r in group),
                "latency": distribution([r["duration_ms"] for r in group if r.get("attempted")]),
                "outcomes": dict(Counter(reason(r) for r in group)),
                "flags": sorted({f for r in group for f in r.get("review_flags", [])}),
                "artifacts": [r["artifact"] for r in group],
            }
        site_rows.append({**case, "providers": per_provider})
    return {"phase": phase, "finished": (root / "finished.json").exists(),
            "total_records": len(rows), "expected_records": len(cases) * phase["repetitions"] * 3,
            "matched_success_observations_per_provider": len(matched), "providers": providers,
            "sites": site_rows, "rows": rows}


def display_ms(summary):
    return f"{summary['median_ms']/1000:.2f}s" if summary.get("n") else "n/a"


def publish(root):
    result = analyze(root)
    (root / "comparison.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    with (root / "timing-by-round.csv").open("w") as output:
        fields = ["provider", "repetition", "attempts", "original_passes", "reviewed_passes",
                  "median_ms", "p90_ms", "p95_ms", "min_ms", "max_ms", "sum_ms",
                  "passed_median_ms", "failed_median_ms"]
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for provider in PROVIDERS:
            for rep in range(result["phase"]["repetitions"]):
                group = [r for r in result["rows"] if r["provider"] == provider
                         and r["repetition"] == rep and r.get("attempted")]
                all_time = distribution([r["duration_ms"] for r in group])
                passed = distribution([r["duration_ms"] for r in group if r["quality_passed"]])
                failed = distribution([r["duration_ms"] for r in group if not r["quality_passed"]])
                writer.writerow({"provider": provider, "repetition": rep, "attempts": len(group),
                                 "original_passes": sum(r["original_live_passed"] for r in group),
                                 "reviewed_passes": sum(r["quality_passed"] for r in group),
                                 **{k: v for k, v in all_time.items() if k in fields},
                                 "passed_median_ms": passed.get("median_ms"),
                                 "failed_median_ms": failed.get("median_ms")})
    with (root / "site-comparison.csv").open("w") as output:
        fields = ["site", "category", "url"] + [p + "_" + v for p in PROVIDERS
                  for v in ["original_passes", "reviewed_passes", "median_ms", "min_ms", "max_ms", "outcomes"]]
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for site in result["sites"]:
            row = {"site": site["id"], "category": site["category"], "url": site["url"]}
            for provider, data in site["providers"].items():
                row.update({provider + "_original_passes": data["original_passes"],
                            provider + "_reviewed_passes": data["quality_passes"],
                            provider + "_outcomes": json.dumps(data["outcomes"]),
                            **{provider + "_" + k: data["latency"].get(k)
                               for k in ["median_ms", "min_ms", "max_ms"]}})
            writer.writerow(row)
    with (root / "requests.csv").open("w") as output:
        fields = ["provider", "site", "repetition", "attempted", "latency_ms", "quality_passed",
                  "original_passed", "outcome", "freshness_evidence", "chars", "flags", "artifact"]
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for row in result["rows"]:
            writer.writerow({"provider": row["provider"], "site": row["id"],
                             "repetition": row["repetition"], "attempted": row.get("attempted"),
                             "latency_ms": row.get("duration_ms"), "quality_passed": row.get("quality_passed"),
                             "original_passed": row.get("original_live_passed"), "outcome": reason(row),
                             "freshness_evidence": row.get("freshness_evidence"), "chars": row.get("characters", 0),
                             "flags": "; ".join(row.get("review_flags", [])), "artifact": row["artifact"]})
    providers = result["providers"]
    summary_rows = ""
    for name, data in providers.items():
        summary_rows += "<tr><th>" + name + "</th><td>" + str(data["quality_passed_by_round"]) + \
            "</td><td>" + str(data["original_live_passed_by_round"]) + "</td><td>" + \
            display_ms(data["all_latency"]) + "</td><td>" + display_ms(data["passed_latency"]) + \
            "</td><td>" + (f"{data['passed_latency']['p95_ms']/1000:.2f}s" if data['passed_latency'].get('n') else 'n/a') + \
            "</td><td>" + display_ms(data["matched_success_latency"]) + "</td></tr>"
    site_rows = ""
    category_rows = ""
    for category in sorted(providers["ours"]["category"]):
        category_rows += "<tr><th>" + category + "</th>"
        for provider in PROVIDERS:
            value = providers[provider]["category"][category]
            category_rows += f"<td>{value['passes']}/{value['total']}</td>"
        category_rows += "</tr>"
    for site in result["sites"]:
        site_rows += '<tr data-search="' + escape(site['id'] + ' ' + site['category']) + '"><th><a href="' + \
            escape(site["url"], quote=True) + '">' + site["id"] + "</a></th>"
        for provider in PROVIDERS:
            data = site["providers"][provider]
            site_rows += '<td><a href="' + escape(data["artifacts"][0] if data['artifacts'] else '#', quote=True) + \
                '">' + str(data["quality_passes"]) + '/' + str(result["phase"]["repetitions"]) + \
                "</a><br>" + display_ms(data["latency"]) + "<small>" + \
                escape(", ".join(data["outcomes"])) + "</small></td>"
        site_rows += "</tr>"
    finished = "Complete" if result["finished"] else "In progress"
    page = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Live 50-site benchmark | Prism</title>
<style>body{margin:0;background:#f6f8fb;color:#182638;font:15px/1.6 system-ui,sans-serif}main{max-width:1200px;margin:auto;padding:36px 22px}h1{font-size:34px;line-height:1.2}h2{margin-top:30px}a{color:#1558a5}table{width:100%;border-collapse:collapse;background:white}td,th{padding:10px;text-align:left;border-bottom:1px solid #dae2eb;vertical-align:top}thead{background:#e4ebf4}small{display:block;color:#58677a;font-size:11px;max-width:220px}.scroll{overflow:auto}.note{padding:16px;background:#fff1d3;border-left:4px solid #b88319}input{padding:10px;width:300px;max-width:90%;margin-bottom:12px;border:1px solid #aebed0;border-radius:6px}code{background:#e3eaf3;padding:2px 5px}.status{color:#58677a}footer{margin-top:30px;color:#58677a}</style>
<main><p class="status">Prism / fresh retrieval / STATUS / RECORDS of EXPECTED records</p><h1>Exa, Firecrawl and our crawler.<br>The same 50 live targets.</h1>
<p><strong>Observed result: Firecrawl leads content coverage. Our local crawler has the lowest median latency on matched successful requests, but remains substantially less reliable across the full set. Exa falls between them on coverage.</strong> The tables retain failures and slow outliers rather than reducing the comparison to a single speed score.</p>
<p class="note">Cache reuse is disabled. Five repetitions use the original URLs and checks. The isolated benchmark disables our robots gate at the user's request; production policy remains unchanged. Access failures are retained. Provider internals and origin/CDN caches cannot be independently controlled.</p>
<h2>Coverage and end-to-end timing</h2><p>Scores are out of 50 per round. “Reviewed” adds readable-text checks and rejects unverified IKEA product identity. Original scores remain visible. Median latency includes every attempted page; passed-page latency excludes refusals and failed content. Matched successes use only the same URL/repetition that passed for all three.</p>
<div class="scroll"><table><thead><tr><th>System</th><th>Reviewed passes / round</th><th>Original passes / round</th><th>All median</th><th>Passed median</th><th>Passed p95</th><th>Matched median</th></tr></thead><tbody>SUMMARY</tbody></table></div>
<p>One request is in flight per provider. The three systems start together for each URL. Free-tier pacing of at least 6.2 seconds between cohorts is excluded from request latency. Actual full benchmark wall time includes pacing, failures, timeouts and artifact collection. This measures controlled per-page latency, not maximum throughput.</p>
<h2>Every target</h2><p>Each cell shows reviewed passes out of five and median latency across attempts. Click a score for a retained response record. Errors and refusals stay in the denominator.</p><input id="search" placeholder="Filter site or category" aria-label="Filter site or category"><div class="scroll"><table id="sites"><thead><tr><th>Original target</th><th>Exa</th><th>Firecrawl</th><th>Ours</th></tr></thead><tbody>SITES</tbody></table></div>
<h2>Coverage by category</h2><p>Reviewed passes across all repeated observations. Each category has five target URLs, tested five times in a completed run.</p><div class="scroll"><table><thead><tr><th>Category</th><th>Exa</th><th>Firecrawl</th><th>Ours</th></tr></thead><tbody>CATEGORIES</tbody></table></div>
<h2>Quality, failures and limitations</h2><p>API success is distinct from target-page success. Challenge pages, empty content, wrong products, short responses, missing required detail and serialized component trees fail the shared checks. Supplemental readable-text checks prevent words appearing only inside URLs from satisfying topic requirements.</p>
<p>The IKEA review exposes a provider returning the category listing under the original product URL. The original checker trusts the returned URL, so that score can be a false positive. We retain that score and separately require evidence of item 00263850. British Museum's history check and Lobsters' point check may reject otherwise usable pages; those original contracts are retained, not silently changed after observing results.</p>
<p>Text-only extraction cannot prove that Windy's forecast controls or map work. No independent semantic precision/recall score is claimed. Provider source completeness cannot be certified from text alone. Our own source verifier remains part of our production path and can reject content another system returns. This is a reused development set, not a new holdout.</p>
<p>Freshness evidence differs: Exa's <code>source=crawled</code> is treated as fresh. Firecrawl's fresh response omits cacheState; freshness relies on its documented <code>maxAge=0</code> contract and the absence of an explicit cache hit. The original receipt fields are preserved. The raw harness's early conservative live_passed flag is not used for reporting; this analyzer normalizes the observed source labels without changing requests, timings or original content scores.</p>
<h2>Reproduce and inspect</h2><p><a href="analysis.md">Detailed analysis</a> · <a href="manual-review.md">Manual spot checks</a> · <a href="comparison.json">All normalized results</a> · <a href="requests.csv">Per-request CSV</a> · <a href="timing-by-round.csv">Timings by round</a> · <a href="site-comparison.csv">Per-site CSV</a> · <a href="phase.json">Frozen configuration</a> · <a href="manifest.json">Original targets</a> · <a href="harness.py">Captured harness</a></p>
<p>Only existing free credits are used. No card or paid upgrade is added. The probe has fixed spending limits and stops on account errors. Timed provider requests ask for plain text/Markdown only, so Firecrawl does not carry an extra raw-HTML download in its timing.</p>
<footer>FINGERPRINT. Five trials per URL support repeatability estimates on this set. Pooled latency distributions include different page difficulty; matched-success distributions are provided to reduce this bias. Provider backend CPU, internal retries and queueing remain opaque.</footer></main>
<script>document.getElementById('search').addEventListener('input',function(){let q=this.value.toLowerCase();document.querySelectorAll('#sites tbody tr').forEach(r=>r.hidden=!r.dataset.search.includes(q))})</script></html>"""
    page = page.replace("STATUS", finished).replace("RECORDS", str(result["total_records"])) \
        .replace("EXPECTED", str(result["expected_records"])) \
        .replace("SUMMARY", summary_rows).replace("SITES", site_rows) \
        .replace("CATEGORIES", category_rows) \
        .replace("FINGERPRINT", result["phase"]["runtime_sha256"])
    (root / "index.html").write_text(page)
    write_analysis(root, result)
    return result


def write_analysis(root, result):
    complete = result["finished"]
    phase, providers = result["phase"], result["providers"]
    lines = ["# Exa, Firecrawl and Prism on the same 50 live targets", "",
             "Status: " + ("complete" if complete else "in progress; partial observations only") + ".", "",
             "Observed verdict: Firecrawl leads reviewed content coverage in this configuration. Our local crawler "
             "has the lowest median latency on the matched successful requests, but much lower full-set reliability. "
             "Exa falls between them on coverage. This is an endpoint/configuration comparison, not an engine-wide "
             "or maximum-throughput verdict.", "",
             "The primary comparison requests a fresh fetch of every original URL, five times per system. "
              "Original URLs and topic/identity checks are retained. These are observed results for this reused "
             "development set, not an unseen holdout or a claim about every website.", "",
             "## Coverage", "",
             "| System | Attempts | Original passes per round, out of 50 | Supplemental reviewed passes |",
             "|---|---:|---|---|"]
    for p, d in providers.items():
        lines.append(f"| {p} | {d['attempted']} | {d['original_live_passed_by_round']} | {d['quality_passed_by_round']} |")
    lines += ["", "Supplemental review removes URL-only keyword matches and flags unverified IKEA product identity. "
              "The IKEA flag is an audit finding exposed by the first live response, not a silent change to the "
              "original score. Missing a topical keyword is not proof that the whole page is unusable. "
              "British Museum's history keyword and Lobsters' point keyword are known limitations of the original checks.", "",
              "## Timing", "",
              "Each timed operation starts after the shared pacing queue. Ours ends after its actual extraction and "
              "verification. Provider timing covers the outbound API request, response download and JSON decoding. "
              "All three start together for each URL. One operation is in flight per provider. URL order is "
              "deterministically shuffled each round, and task scheduling order rotates. Provider connections remain "
              "pooled; our local database and browser state start fresh each round. Provider internal pools are opaque.", "",
              "| System | All attempts median | All p90 | All p95 | Minimum | Maximum | Passed median | Passed p95 | Matched-success median |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for p, d in providers.items():
        all_time, passed = d["all_latency"], d["passed_latency"]
        values = [all_time.get(k) for k in ["median_ms", "p90_ms", "p95_ms", "min_ms", "max_ms"]]
        values += [passed.get("median_ms"), passed.get("p95_ms"), d["matched_success_latency"].get("median_ms")]
        lines.append("| " + p + " | " + " | ".join(f"{v / 1000:.3f}s" if v is not None else "n/a" for v in values) + " |")
    lines += ["", f"Matched-success observations per system: {result['matched_success_observations_per_provider']}. "
              "These match URL and repetition across all three systems. Successful-only medians otherwise cover "
              "different sets of pages. Fast refusals are retained in all-attempt timing and separately reported as failures.", "",
              "The benchmark wall time is coupled across systems and includes free-tier pacing and artifacts. "
              "The local crawler's run-level elapsed time includes waiting for the provider tasks, so it is not "
              "reported as standalone crawler throughput. The 6.2-second cohort interval respects Firecrawl's "
              "10-RPM Free limit. This is a latency study at controlled concurrency, not a maximum-throughput race. "
              "No service-wide speed winner should be inferred from different success sets or from one site's result.", "",
              "## Failures and quality", ""]
    for p, d in providers.items():
        lines += [f"### {p}", "", f"Observed failures: `{json.dumps(d['failure_counts'], ensure_ascii=False)}`.", "",
                  f"Freshness evidence: `{json.dumps(d['freshness_evidence'], ensure_ascii=False)}`.", ""]
    for case_id in ["ikea", "khanacademy", "patagonia", "nhk-ja", "windy", "mathworld", "coursera"]:
        site = next(s for s in result["sites"] if s["id"] == case_id)
        lines.append(f"- **{case_id}**: " + "; ".join(
            p + " " + str(site["providers"][p]["quality_passes"]) + "/" + str(phase["repetitions"]) +
            " (" + ", ".join(site["providers"][p]["outcomes"]) + ")" for p in PROVIDERS) + ".")
    if complete:
        lines += ["", "Important local outliers:", ""]
        for case_id in ["archive", "openstax", "windy"]:
            site = next(s for s in result["sites"] if s["id"] == case_id)
            data = site["providers"]["ours"]
            lines.append(f"- {case_id}: {data['quality_passes']}/5 reviewed successes, "
                         f"median across attempts {data['latency']['median_ms']/1000:.2f}s. "
                         f"Observed outcomes: {data['outcomes']}.")
        lines += ["", "Archive's four failed local runs reach the 90-second page deadline after the initial "
                  "HTTP response produces no usable main content and browser recovery does not finish in budget. "
                  "OpenStax and Windy pass but their browser path is slow. These failures and tails are a real "
                  "weakness of the current implementation, despite its fast median on straightforward HTTP pages.", ""]
    lines += ["", "Quality assessment includes API/target failures, visible challenge pages, minimum content, topic "
              "markers, exact-target URL review, readable text after removing URLs, serialized JSON rejection, "
              "duplicate lines and output size. Larger output is not automatically better. There is no independently "
              "annotated semantic gold standard, and provider text alone cannot certify source completeness. "
              "Our own bounded source verifier is part of our real pipeline and can reject fragments that another "
              "system returns. Windy's text checks do not certify map or forecast interactions.", "",
              "## Freshness and policy", "",
              "Exa requests use `maxAgeHours: 0` and a 90-second livecrawl timeout; the observed source label "
              "`crawled` is normalized as fresh. Firecrawl requests use `maxAge: 0`, normal `proxy: auto`, "
              "Markdown only, 90-second target timeout, verified TLS and no saved profile. Its fresh responses "
              "omit cacheState; freshness relies on the documented API contract and any explicit cache hit would "
              "be excluded. Initial raw receipt labels and the early conservative live_passed fields are preserved "
              "unchanged; the reporting normalizer is explicit. Neither API's internal origin fetch can be independently observed.", "",
              "Our isolated benchmark overrides robots decisions, at the user's explicit request. Scope, public-IP "
              "validation, GET/HEAD restrictions, rate/backoff handling, extraction checks and access-wall stops remain. "
              "Production crawler source and its default robots policy are unchanged. Firecrawl's vendor-managed "
              "proxy behavior is its documented default; Exa's routing and access policy are opaque. The systems "
              "therefore do not have identical underlying networking or access methods. No CAPTCHA solver, credentials "
              "for target websites, login state or paid proxy is added to our crawler. Origin/CDN and asset caches "
              "are outside the experiment's control even when result-cache reuse is disabled.", "",
              "## Cost", "",
              f"Exa response-reported charge: ${providers['exa']['reported_charge']:.3f} from existing free credits. "
              f"Firecrawl response-reported usage: {providers['firecrawl']['reported_charge']:g} credits. "
              "Account before/after quota receipts are authoritative for Firecrawl; any discrepancy with summed "
              "response metadata must be investigated. No payment method, Smart Upgrade, paid plan or top-up is enabled. "
              "Hard spending caps are 600 Firecrawl credits and $1 of Exa's verified free balance. Local compute "
              "has no API fee. Provider billing for an in-flight timeout can arrive after the client stops waiting.", "",
              "## Reproduce and inspect", "",
              "- `phase.json` records configuration, code hashes, runtime, limits and the measurement claim.",
              "- `manifest.json` and `quality-contract.json` retain the original targets and checks.",
              "- `run-*/<provider>-<site>.json` retain every operation, document, duration and review.",
              "- `run-*/ours-events.json` retain our HTTP events, extraction attempts and captured bodies.",
              "- `bodies/` retains hash-addressed, compressed provider responses and local captures.",
              "- `requests.csv` is the flat timing and outcome export.",
              "- `timing-by-round.csv` separates each system's five latency and coverage distributions.",
              "- `site-comparison.csv` is the complete 50-target comparison with median and range.",
              "- `manual-review.md` records concrete, first-round content spot checks and their limitations.",
              "- `comparison.json` includes medians, tails, per-category coverage and per-site repeatability.",
              "- `quota-before.json` and `quota-after-*.json` retain Firecrawl balances.", "",
              "Regenerate the analysis offline with:", "", "```sh",
              f".venv/bin/python -m benchmarks.provider_live_report --root {root} --verify", "```", "",
              "Official references: [Exa freshness](https://exa.ai/docs/contents/quickstart), "
              "[Firecrawl scrape controls](https://docs.firecrawl.dev/api-reference/endpoint/scrape), "
              "[Firecrawl Free rate limits](https://docs.firecrawl.dev/rate-limits).", ""]
    (root / "analysis.md").write_text("\n".join(lines))


def verify(root):
    count = 0
    for row in load_rows(root):
        receipt = row.get("receipt", {})
        sha = receipt.get("response_sha256")
        if sha:
            data = gzip.decompress((root / "bodies" / (sha + ".gz")).read_bytes())
            assert hashlib.sha256(data).hexdigest() == sha
            count += 1
        if row.get("attempted") and row["provider"] == "exa":
            assert receipt["request"]["maxAgeHours"] == 0
        if row.get("attempted") and row["provider"] == "firecrawl":
            assert receipt["request"]["maxAge"] == 0
    return count


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("reports/provider-live50"))
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    result = publish(args.root)
    print(json.dumps({"finished": result["finished"], "rows": result["total_records"],
                      "scores": {p: d["quality_passed_by_round"] for p, d in result["providers"].items()}}))
    if args.verify:
        print("Provider responses verified:", verify(args.root))
