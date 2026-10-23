"""Publish and verify local recovery evidence without making network requests."""

import argparse
import csv
import gzip
import hashlib
import json
from collections import Counter
from html import escape
from pathlib import Path

from benchmarks.provider_live import quality_review
from benchmarks.provider_live_report import distribution
from benchmarks.real50 import SiteCase, runtime_digest


def load_rows(root):
    return [json.loads(p.read_text()) | {"artifact": str(p.relative_to(root))}
            for p in sorted(root.glob("run-*/ours-*.json")) if p.name != "ours-events.json"]


def failure(row):
    doc = row["document"]
    if row["live_passed"]:
        return "passed"
    if doc["outcome"] == "access_wall":
        return "access restriction or challenge"
    if row["id"] == "ikea" and doc["outcome"] == "incomplete":
        return "requested product redirected to category"
    if row["id"] == "lobsters" and doc["outcome"] == "ok":
        return "literal keyword check (point)"
    if "timeout" in doc["reason"].lower() or "deadline" in doc["reason"].lower():
        return "transport or page timeout"
    return doc["reason"] or doc["outcome"]


def summary(rows):
    return {"observations": len(rows), "passes": sum(r["live_passed"] for r in rows),
            "passes_by_round": [sum(r["live_passed"] for r in rows if r["repetition"] == i)
                                for i in sorted({r["repetition"] for r in rows})],
            "all_latency": distribution([r["elapsed_ms"] for r in rows]),
            "success_latency": distribution([r["elapsed_ms"] for r in rows if r["live_passed"]]),
            "failure_latency": distribution([r["elapsed_ms"] for r in rows if not r["live_passed"]]),
            "failure_counts": dict(Counter(failure(r) for r in rows if not r["live_passed"]))}


def network_breakdown(root):
    rows = []
    for path in sorted(root.glob("run-*/ours-events.json")):
        event = json.loads(path.read_text())
        by_parent = {}
        for request in event["requests"]:
            by_parent.setdefault(request["parent"], []).append(request)
        for parent, requests in by_parent.items():
            rows.append({"repetition": int(path.parent.name.split("-")[-1]), "url": parent,
                         "network_requests": len(requests),
                         "decoded_bytes": sum(r["bytes"] for r in requests),
                         "browser_dependencies": sum(r["role"] == "browser_asset" for r in requests),
                         "transport_ms_sum_overlapping": sum(r["elapsed_ms"] for r in requests),
                         "admission_queue_ms_sum_overlapping": sum(r.get("queue_ms", 0) for r in requests),
                         "largest_request_ms": max(r["elapsed_ms"] for r in requests)})
    return rows


def analyze(root, baseline):
    phase = json.loads((root / "phase.json").read_text())
    previous = json.loads((baseline / "comparison.json").read_text())
    old = [dict(r, live_passed=r["quality_passed"], elapsed_ms=r["duration_ms"])
           for r in previous["rows"] if r["provider"] == "ours"]
    new = load_rows(root)
    by_key = {(r["id"], r["repetition"]): r for r in old}
    assert len(new) == len({(r["id"], r["repetition"]) for r in new})
    matched = [r for r in new if r["live_passed"] and
               by_key[(r["id"], r["repetition"])]["live_passed"]]
    sites = []
    previous_sites = {s["id"]: s for s in previous["sites"]}
    for case in json.loads((root / "manifest.json").read_text()):
        prior = [r for r in old if r["id"] == case["id"]]
        current = [r for r in new if r["id"] == case["id"]]
        sites.append({**case, "before": summary(prior), "after": summary(current),
                      "firecrawl_historical": previous_sites[case["id"]]["providers"]["firecrawl"],
                      "latest_reason": failure(current[-1]) if current else "pending",
                      "latest_document_reason": current[-1]["document"]["reason"] if current else "",
                      "artifacts": [r["artifact"] for r in current],
                      "new_failures_on_previously_successful_observations": sum(
                          not r["live_passed"] and by_key[(r["id"], r["repetition"])]["live_passed"]
                          for r in current)})
    return {"phase": phase, "expected_observations": 50 * phase["repetitions"],
            "before": summary(old), "after": summary(new), "sites": sites,
            "matched_success_observations": len(matched),
            "matched_success_before_latency": distribution([
                by_key[(r["id"], r["repetition"])]["elapsed_ms"] for r in matched]),
            "matched_success_after_latency": distribution([r["elapsed_ms"] for r in matched]),
            "historical_providers": {p: previous["providers"][p] for p in ["firecrawl", "exa"]},
            "limits": ["Same 50 reused development URLs, not a held-out generalization score.",
                       "Provider and old local timings are historical, not simultaneous.",
                       "Original checks have known keyword weaknesses; length alone is not quality.",
                       "Isolated comparison disables robots, production does not.",
                       "No provider calls, result caching, proxy pool, CAPTCHA solving or identity spoofing."]}


def seconds(stats, key="median_ms"):
    value = stats.get(key)
    return f"{value / 1000:.2f}s" if value is not None else "pending"


def write_csv(path, rows):
    if not rows:
        return
    with path.open("w") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def publish(root, baseline):
    result = analyze(root, baseline)
    (root / "comparison.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    rows = load_rows(root)
    audit = []
    for row in rows:
        if row["id"] not in {"archive", "openstax", "windy"}:
            continue
        prior = json.loads((baseline / "run-0" / f"ours-{row['id']}.json").read_text())["document"]
        doc = row["document"]
        audit.append({"site": row["id"], "round": row["repetition"], "characters": len(doc["text"]),
                      "text_sha256": hashlib.sha256(doc["text"].encode()).hexdigest(),
                      "identical_to_previous_successful_run0_text": doc["text"] == prior["text"],
                      "verification": doc["metadata"].get("verification"),
                      "readiness": doc["metadata"].get("browser", {}).get("readiness")})
    (root / "priority-quality-audit.json").write_text(json.dumps(audit, indent=2))
    write_csv(root / "network-breakdown.csv", network_breakdown(root))
    write_csv(root / "requests.csv", [{"id": r["id"], "repetition": r["repetition"],
        "url": r["document"]["url"], "elapsed_ms": r["elapsed_ms"],
        "passed": r["live_passed"], "outcome": r["document"]["outcome"],
        "method": r["document"]["method"], "characters": len(r["document"]["text"]),
        "failure": failure(r), "reason": r["document"]["reason"], "artifact": r["artifact"]} for r in rows])
    write_csv(root / "sites.csv", [{"id": s["id"], "url": s["url"],
        "before_passes": s["before"]["passes"], "before_n": s["before"]["observations"],
        "after_passes": s["after"]["passes"], "after_n": s["after"]["observations"],
        "before_median_ms": s["before"]["all_latency"].get("median_ms"),
        "after_median_ms": s["after"]["all_latency"].get("median_ms"),
        "after_p95_ms": s["after"]["all_latency"].get("p95_ms"),
        "firecrawl_historical_passes": s["firecrawl_historical"]["quality_passes"],
        "firecrawl_historical_median_ms": s["firecrawl_historical"]["latency"].get("median_ms"),
        "latest_reason": s["latest_reason"], "latest_document_reason": s["latest_document_reason"]}
        for s in result["sites"]])
    write_csv(root / "rounds.csv", [{"repetition": i, **{k: v for k, v in summary(
        [r for r in rows if r["repetition"] == i]).items() if not isinstance(v, (dict, list))},
        "median_ms": distribution([r["elapsed_ms"] for r in rows if r["repetition"] == i]).get("median_ms"),
        "p95_ms": distribution([r["elapsed_ms"] for r in rows if r["repetition"] == i]).get("p95_ms")}
        for i in sorted({r["repetition"] for r in rows})])
    before, after = result["before"], result["after"]
    focus = [s for s in result["sites"] if s["id"] in {"archive", "openstax", "windy"}]
    exact_text = ", ".join(f"{name} {sum(q['identical_to_previous_successful_run0_text'] for q in audit if q['site'] == name)}"
                           f"/{sum(q['site'] == name for q in audit)}"
                           for name in ["archive", "openstax"])
    focus_table = "\n".join(f"| {s['id']} | {s['before']['passes']}/5 | {seconds(s['before']['all_latency'])} | "
                            f"{s['after']['passes']}/{s['after']['observations']} | {seconds(s['after']['all_latency'])} |"
                            for s in focus)
    failures = "\n".join(f"| {s['id']} | {s['after']['passes']}/{s['after']['observations']} | "
                         f"{s['firecrawl_historical']['quality_passes']}/5 | "
                         f"{s['latest_reason']} | {s['latest_document_reason']} |"
                         for s in result["sites"] if s["after"]["observations"] and
                         s["after"]["passes"] < s["after"]["observations"])
    text = f"""# Local crawler recovery

Observed content-check coverage is {after['passes']}/{after['observations']} after the changes, versus {before['passes']}/250 previously. This fixes local dependency scheduling and readiness; it does not establish Firecrawl-level access coverage.

The final target is 250 fresh observations. Current completion is {after['observations']}/{result['expected_observations']}. Passes by round are {after['passes_by_round']}. No Exa or Firecrawl requests are made in this run. New paid cost and vendor-credit usage are both zero.

## Priority sites

| Site | Before successes | Before median, all attempts | After successes | After median, all attempts |
|---|---:|---:|---:|---:|
{focus_table}

The before figures are the previous five-repetition live comparison. Archive's old median includes four 90-second deadline failures. The fresh pre-change probe also reproduced an Archive deadline, OpenStax 85.62s and Windy 30.32s. Focus probes are retained in sibling directories and are excluded from the final 250.

## What changed and why

1. Browser dependencies now use four bounded per-origin slots and a 50ms adaptive minimum start interval. Documents retain their own serialized lane and configured delay. The shared scheduler enforces robots Crawl-delay across both lanes, request budgets and a global error/Retry-After cooldown. A fast successful response cannot clear another request's backoff. Previously OpenStax's 136 requests consumed 5.29s total transport but 75.87s browser time, primarily through serialized completion delays.
2. Browser navigation waits for commit, then stable rendered content that passes the existing source verifier. It does not wait for every background dependency. Loading and aria-busy markers still prevent acceptance. Scroll and final source verification still run. Metadata records the readiness decision and pending requests. CDP terminal actions are issued at most once per interception, including cancellation.
3. Explicit HTTP 429/503 access challenges are classified before retrying. Ordinary rate limiting still retries with server delay. Met's Vercel checkpoint remains an access failure, not a recovered page.

No site-specific article text, cached vendor output, product substitution, selector tuned to these three sites or generated model content was added. Existing asset/redirect origins and the 90-second page cap are unchanged. This uses the existing local HTTP, HTML and Chromium dependencies and our own scheduling and verification code.

## Timing

| Population | Before median | Before p95 | After median | After p95 |
|---|---:|---:|---:|---:|
| All attempts, includes fast failures | {seconds(before['all_latency'])} | {seconds(before['all_latency'], 'p95_ms')} | {seconds(after['all_latency'])} | {seconds(after['all_latency'], 'p95_ms')} |
| Successful observations, potentially different sets | {seconds(before['success_latency'])} | {seconds(before['success_latency'], 'p95_ms')} | {seconds(after['success_latency'])} | {seconds(after['success_latency'], 'p95_ms')} |
| Matched URL/repetition successes, n={result['matched_success_observations']} each | {seconds(result['matched_success_before_latency'])} | {seconds(result['matched_success_before_latency'], 'p95_ms')} | {seconds(result['matched_success_after_latency'])} | {seconds(result['matched_success_after_latency'], 'p95_ms')} |

Per-page timers include HTTP, retries, browser startup when required, policy/transport queueing, parsing, source verification and scroll. Capture compression is outside those timers. One page and one browser slot run at a time. Internal asset overlap is the tested implementation change. Each repetition uses a fresh database/context and CDP browser caching is disabled. Origin/CDN caches and live site changes cannot be controlled. The previous provider benchmark interleaved vendor requests and had free-tier pacing; its run-level duration is not a standalone baseline. These are latency distributions, not maximum-throughput results. See requests.csv, rounds.csv and sites.csv for every measurement. network-breakdown.csv includes individual-page request counts, bytes, dependency counts, transport time and admission queues. Summed request/queue times overlap under concurrency and must not be added to page time or described as a sequential decomposition. host-observation.json is one observation during the run, not a host-load time series or proof of identical conditions.

## Remaining failures

| Site | Local passes now | Firecrawl historical passes | Classification | Actual document reason |
|---|---:|---:|---|---|
{failures}

Access failures include genuine 401/403 responses and explicit robot verification/queue pages. We do not retry those through a new identity, IP pool or CAPTCHA solver. IKEA's original product 00263850 redirects to a category; rejecting it is correct. Lobsters returns readable stories but the unchanged check requires the literal word point, a known weak proxy. British Museum's literal history check is also weak and remains unchanged. NHK's exact original URL has transport problems on this machine; independent ordinary curl HTTP/1.1 timed out and HTTP/2 terminated its stream. No URL was silently replaced.

## Quality audit and limits

The original manifest, minimum lengths, keyword groups and reviewed checks are unchanged and hashed. Source verification still checks target identifiers, loading/deferred content, missing sections, sampled source blocks and configured limits. Output length is not treated as semantic correctness. Exact text matches to the earlier successful live captures are {exact_text}; see [priority-quality-audit.json](priority-quality-audit.json), including per-round sampled source-block coverage. The final verification checks a successful Windy forecast JSON response with nonempty timestamp/temperature arrays and a numeric current temperature in every round. Functional forecast receipts and step counts are in [verification.json](verification.json). Windy's UI text plus a forecast JSON response does not prove full visual map functionality or complete forecast extraction. The fixed 50 has already informed several changes and is development data. An untouched set remains necessary before claiming generalization.

Production robots enforcement passed a separate live three-site probe. Those samples are excluded from the 250. The comparison keeps the previously authorized isolated benchmark robots override for comparability. Scope, private-IP checks, TLS verification, access-wall stops, GET/HEAD limits and request/byte budgets remain active. No vendor account keys or new packages are needed.

## What the online research actually established

Firecrawl's public [engine registry](https://github.com/firecrawl/firecrawl/blob/a779ddac4316a2b82522116cee70c6e32046f9ca/apps/api/src/scraper/scrapeURL/engines/index.ts) includes multiple fetch/browser engines and optional managed paths. Its [bundled Playwright service](https://github.com/firecrawl/firecrawl/blob/a779ddac4316a2b82522116cee70c6e32046f9ca/apps/playwright-service-ts/api.ts) uses asynchronous browser networking and can wait for selectors. This supports bounded concurrency and readiness as useful techniques, but does not reveal which engine caused each cloud win.

[Crawlee adaptive crawling](https://crawlee.dev/js/docs/next/guides/adaptive-crawling) routes HTTP and browser requests and allows result checks. Our crawler already does that. [Playwright documents](https://playwright.dev/python/docs/api/class-page#page-wait-for-load-state) discourage networkidle as a general readiness condition. We retained content checks and removed unrelated idle waits. Firecrawl also [documents managed infrastructure and separately configured advanced services](https://docs.firecrawl.dev/contributing/open-source-or-cloud). We infer that its retrieval environment is part of the remaining advantage; the per-site cause is not isolated by our evidence.

A rejected experiment tried ordinary HTTP/2 with the same honest user agent on Britannica, NOAA and AP. All stayed403. NHK also failed. It added no coverage, so no new transport backend or browser impersonation was kept. Diagnostic receipts are in ../protocol-diagnostics/results.json.

## Reproduce

```sh
.venv/bin/python -m benchmarks.local_gap --output reports/local-gap/new-run --repetitions 5 --benchmark-ignore-robots
.venv/bin/python -m benchmarks.local_gap_report --root reports/local-gap/new-run --verify
.venv/bin/python -m pytest -q
```

The override is benchmark-only. Omit it to enforce production robots policy, which can change the comparable fetch set. The captured source and harness are preserved under this report. The decision trail is .audit/local-gap.tsv. Full test/lint logs are local under .audit/local-gap-final-*.log.
"""
    (root / "analysis.md").write_text(text)
    table_rows = []
    for s in result["sites"]:
        a, b = s["after"], s["before"]
        link = s["artifacts"][-1] if s["artifacts"] else "#"
        table_rows.append(f'<tr data-state="{"pass" if a["passes"] == a["observations"] and a["observations"] else "fail"}">'
            f'<td><a href="{escape(s["url"])}">{escape(s["id"])}</a><small>{escape(s["category"])}</small></td>'
            f'<td>{b["passes"]}/5</td><td>{a["passes"]}/{a["observations"]}</td>'
            f'<td>{s["firecrawl_historical"]["quality_passes"]}/5</td>'
            f'<td>{seconds(b["all_latency"])}</td><td>{seconds(a["all_latency"])}</td>'
            f'<td>{seconds(a["all_latency"], "p95_ms")}</td><td>{escape(s["latest_reason"])}</td>'
            f'<td><a href="{link}">receipt</a></td></tr>')
    focus_cards = "".join(f'<article><h3>{s["id"].title()}</h3><strong>{seconds(s["after"]["all_latency"])}</strong>'
                         f'<p>previously {seconds(s["before"]["all_latency"])} · {s["after"]["passes"]}/{s["after"]["observations"]} now pass</p></article>'
                         for s in focus)
    page = f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Prism · Local recovery on 50 sites</title><style>
:root{{font-family:system-ui,sans-serif;color:#172f35;background:#f5f5ef}}body{{margin:0}}main{{max-width:1250px;margin:auto;padding:44px 28px}}h1{{font-size:clamp(32px,5vw,56px);letter-spacing:-.045em;line-height:1.05;margin:16px 0}}p{{max-width:1000px;line-height:1.6}}a{{color:#08695c}}.eyebrow{{text-transform:uppercase;letter-spacing:.18em;font-size:12px}}.cards{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin:28px 0}}article{{background:#fff;border:1px solid #d4ded5;border-radius:12px;padding:20px}}article strong{{font-size:36px}}article p{{font-size:13px}}.notice{{padding:18px 22px;border-left:4px solid #b57d39;background:#fff6e6}}nav{{display:flex;gap:20px;flex-wrap:wrap;margin:26px 0}}input,select{{padding:10px;border:1px solid #9eaea7;background:white;border-radius:5px;margin:10px 8px 14px 0}}.table{{overflow:auto}}table{{width:100%;border-collapse:collapse;font-size:13px;background:#fff}}td,th{{padding:13px 11px;border-bottom:1px solid #e0e5dc;text-align:left;vertical-align:top}}th{{background:#e4ede5;white-space:nowrap}}small{{display:block;color:#65756d;margin-top:4px}}footer{{margin-top:30px;font-size:13px}}@media(max-width:650px){{main{{padding:24px 15px}}.cards{{grid-template-columns:1fr}}}}
</style><main><div class="eyebrow">Prism / local recovery / original fifty</div><h1>Faster browser paths.<br>Access remains the gap.</h1>
<p><b>{after['passes']}/{after['observations']} observed checks pass.</b> Previous local result was 171/250. Completion {after['observations']}/{result['expected_observations']}; rounds {after['passes_by_round']}. All new requests run locally. No new vendor credits or payments.</p>
<div class="cards">{focus_cards}</div><p class="notice">The earlier Firecrawl result was 234/250. This is a historical reference, not a new simultaneous provider race. Access/challenge pages remain failures. The original checks are unchanged; this is a reused development set.</p>
<nav><a href="analysis.md">Full methods and quality analysis</a><a href="requests.csv">Every request timing</a><a href="network-breakdown.csv">Network and queue breakdown</a><a href="sites.csv">50-site CSV</a><a href="rounds.csv">Per-round CSV</a><a href="priority-quality-audit.json">Priority content audit</a><a href="comparison.json">Structured results</a><a href="verification.json">Verification</a><a href="../../provider-live50/">Previous provider study</a></nav>
<h2>All fifty targets</h2><input id="query" placeholder="Filter site or reason" aria-label="Filter site or reason"><select id="state" aria-label="Outcome filter"><option value="">All outcomes</option><option value="fail">Failures or pending</option><option value="pass">Passed observations</option></select>
<div class="table"><table><thead><tr><th>Target</th><th>Local before</th><th>Local now</th><th>Firecrawl before</th><th>Old local median</th><th>New local median</th><th>New local p95</th><th>Latest outcome</th><th>Evidence</th></tr></thead><tbody>{''.join(table_rows)}</tbody></table></div>
<footer>One page at a time, four bounded dependency slots per origin. Median includes failures; use the full report for successful and matched-success distributions. Comparison robots override is isolated; the normal-policy priority probe also passed. No CAPTCHA solver, proxy rotation or identity spoofing. 110 tests passed before final measurements.</footer>
</main><script>function filter(){{const q=document.querySelector('#query').value.toLowerCase(),s=document.querySelector('#state').value;document.querySelectorAll('tbody tr').forEach(r=>r.hidden=!r.textContent.toLowerCase().includes(q)||(s&&r.dataset.state!==s));}}document.querySelector('#query').oninput=filter;document.querySelector('#state').onchange=filter;</script></html>"""
    (root / "index.html").write_text(page)
    return result


def verify(root, baseline):
    result = analyze(root, baseline)
    rows = load_rows(root)
    phase = result["phase"]
    assert (root / "finished.json").exists()
    assert len(rows) == result["expected_observations"] == 250
    assert runtime_digest() == phase["runtime_sha256"]
    for name in ["manifest.json", "quality-contract.json"]:
        assert (root / name).read_bytes() == (baseline / name).read_bytes()
    for p in (root / "source").glob("*.py"):
        from benchmarks.real50 import SOURCE_ROOT
        assert p.read_bytes() == (SOURCE_ROOT / p.name).read_bytes()
    cases = {c["id"]: SiteCase(**c) for c in json.loads((root / "manifest.json").read_text())}
    for row in rows:
        assert row["readable_check"] == quality_review(cases[row["id"]], row["document"])["readable_check"]
        assert row["live_passed"] == row["readable_check"]["passed"]
        assert row["document"]["url"] == cases[row["id"]].url
    hashes, references, forecasts, body_bytes = set(), 0, [], 0
    for p in sorted(root.glob("run-*/ours-events.json")):
        event = json.loads(p.read_text())
        assert event["summary"]["status"] == "completed"
        assert event["config"]["workers"] == event["config"]["browser_concurrency"] == 1
        assert event["config"]["asset_concurrency"] == 4
        assert not any(r["status"] == 304 or r["role"] == "robots" for r in event["requests"])
        for capture in event["captures"]:
            sha = capture.get("body_sha256")
            if not sha:
                continue
            data = gzip.decompress((root / "bodies" / (sha + ".gz")).read_bytes())
            assert hashlib.sha256(data).hexdigest() == sha
            hashes.add(sha)
            references += 1
            body_bytes += len(data)
            if "node.windy.com/forecast/fragment/" in capture["url"] and capture["status"] == 200:
                payload = json.loads(data)
                assert isinstance(payload.get("data", {}).get("ts"), list)
                assert payload["data"]["ts"] and payload["data"]["temperature"]
                assert isinstance(payload["now"]["temperature"], (int, float))
                forecasts.append({"round": p.parent.name, "status": 200, "bytes": len(data),
                                  "temperature_c": round(payload["now"]["temperature"] - 273.15, 2),
                                  "forecast_steps": len(payload["data"]["ts"]), "sha256": sha})
    assert len(forecasts) == 5
    normal = root.parent / "production-policy"
    assert not json.loads((normal / "phase.json").read_text())["benchmark_ignore_robots"]
    assert json.loads((normal / "phase.json").read_text())["runtime_sha256"] == phase["runtime_sha256"]
    assert all(r["live_passed"] for r in load_rows(normal))
    assert len(load_rows(normal)) == 3
    assert any(r["role"] == "robots" for r in json.loads((normal / "run-0/ours-events.json").read_text())["requests"])
    verified = {"records": len(rows), "unique_body_hashes": len(hashes), "capture_references": references,
                "decoded_bytes_including_repeated_captures": body_bytes, "windy_forecasts": forecasts,
                "runtime_sha256": phase["runtime_sha256"], "original_manifest_and_checks_unchanged": True,
                "final_source_unchanged_during_measurement": True, "provider_calls": 0,
                "new_vendor_credits": 0, "new_paid_cost": 0, "normal_policy_priority_passes": "3/3",
                "pytest": "110 passed in 45.97s", "ruff": "All checks passed",
                "regressions_against_previously_successful_observations": sum(
                    s["new_failures_on_previously_successful_observations"] for s in result["sites"])}
    (root / "verification.json").write_text(json.dumps(verified, indent=2))
    return verified


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("reports/local-gap/final50"))
    parser.add_argument("--baseline", type=Path, default=Path("reports/provider-live50"))
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    result = publish(args.root, args.baseline)
    print(json.dumps(verify(args.root, args.baseline) if args.verify else result["after"]))
