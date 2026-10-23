"""Generate a comparison without changing the original scoring contract."""

import csv
import hashlib
import html
import json
import re
import shutil
import statistics
from pathlib import Path
from urllib.parse import urlsplit

from .real50 import normalize, runtime_digest, write_json
from .real50_review import reviewed_checks

ROOT = Path("reports/technical-final")
NAMES = {"http": "HTTP only", "browser": "Browser first", "adaptive": "Adaptive"}


def esc(value):
    return html.escape(str(value), quote=True)


def label(values):
    return str(values[0]) if len(set(values)) == 1 else f"{min(values)} to {max(values)}"


def functional_checks(record):
    rules = json.loads(Path("benchmarks/technical-review.json").read_text())
    cases = {c["id"]: c for c in json.loads(Path("benchmarks/real50.json").read_text())}
    docs = {d["url"]: d for d in record["documents"]}
    checks = {}
    for key, rule in rules.items():
        if key == "basis":
            continue
        doc = docs[cases[key]["url"]]
        text = normalize(doc["text"])
        issues = []
        if doc["outcome"] != "ok" or len(doc["text"]) < rule["minimum_chars"]:
            issues.append("Missing substantive content")
        for group in rule["groups"]:
            if not any(normalize(word) in text for word in group):
                issues.append("Missing " + " / ".join(group))
        if any(normalize(word) in text for word in rule.get("forbidden", [])):
            issues.append("Application failure message present")
        for field, method in (("requires_successful_request_path", None), ("requires_successful_head_path", "HEAD")):
            path = rule.get(field)
            if path and not any(path in urlsplit(q["url"]).path and q["parent"] == doc["url"]
                                and 200 <= q["status"] < 300 and q["outcome"] == "ok"
                                and (method is None or q.get("method") == method)
                                for q in record["requests"]):
                issues.append("No successful guarded request for " + path)
        checks[key] = {"passed": not issues, "issues": issues}
    return checks


def load(directory, supplemental=False):
    records = [json.loads((directory / f"run-{i}-{strategy}.json").read_text())
               for i in range(5) for strategy in NAMES]
    for record in records:
        record["reviewed"] = record["checks"] if supplemental else reviewed_checks(record)
        if not supplemental:
            record["functional"] = functional_checks(record)
    groups = {}
    for strategy in NAMES:
        runs = [r for r in records if r["strategy"] == strategy]
        seconds = [r["summary"]["elapsed_ms"] / 1000 for r in runs]
        groups[strategy] = {
            "reviewed_passes": [sum(c["passed"] for c in r["reviewed"]) for r in runs],
            "topic_passes": [sum(c["passed"] for c in r["checks"]) for r in runs],
            "median_seconds": statistics.median(seconds), "range_seconds": [min(seconds), max(seconds)],
            "requests": [r["summary"]["requests"] for r in runs],
            "browser_pages": [r["summary"]["browser_pages"] for r in runs],
            "bytes": [r["summary"]["bytes"] for r in runs],
            "per_site": {c["id"]: sum(next(x for x in r["reviewed"] if x["id"] == c["id"])["passed"]
                                      for r in runs) for c in runs[0]["reviewed"]},
        }
        if not supplemental:
            groups[strategy]["functional"] = {key: sum(r["functional"][key]["passed"] for r in runs)
                                              for key in runs[0]["functional"]}
    return records, groups


def main():
    records, groups = load(ROOT / "after")
    exa_records, exa_groups = load(ROOT / "exa", supplemental=True)
    previous = json.loads(Path("reports/real50/comparison.json").read_text())["after"]
    metadata = json.loads((ROOT / "after/phase.json").read_text())
    assert metadata["runtime_sha256"] == runtime_digest()
    cases = json.loads(Path("benchmarks/real50.json").read_text())
    first = {s: next(r for r in records if r["repetition"] == 0 and r["strategy"] == s) for s in NAMES}
    comparison = {
        "kind": "Same 50-site development set after technical recovery; Exa supplemental",
        "metadata": metadata, "previous": previous["results"], "current": groups,
        "exa": exa_groups, "runtime_sha256": runtime_digest(),
        "manifest_sha256": hashlib.sha256(Path("benchmarks/real50.json").read_bytes()).hexdigest(),
        "review_sha256": hashlib.sha256(Path("benchmarks/real50-review.json").read_bytes()).hexdigest(),
        "functional_sha256": hashlib.sha256(Path("benchmarks/technical-review.json").read_bytes()).hexdigest(),
    }
    rows, remaining, improvements, regressions = [], [], [], []
    for case in cases:
        cells = []
        improved = any(groups[s]["per_site"][case["id"]] > previous["results"][s]["per_site"][case["id"]] for s in NAMES)
        regressed = any(groups[s]["per_site"][case["id"]] < previous["results"][s]["per_site"][case["id"]] for s in NAMES)
        if improved:
            improvements.append(case["id"])
        if regressed:
            regressions.append(case["id"])
        failed = groups["adaptive"]["per_site"][case["id"]] < 5
        for strategy in NAMES:
            count = groups[strategy]["per_site"][case["id"]]
            before = previous["results"][strategy]["per_site"][case["id"]]
            sample = next((r for r in records if r["strategy"] == strategy and any(
                c["id"] == case["id"] and not c["passed"] for c in r["reviewed"])), first[strategy])
            check = next(c for c in sample["reviewed"] if c["id"] == case["id"])
            doc = next(d for d in sample["documents"] if d["url"] == case["url"])
            reason = "; ".join(filter(None, [check["reason"], *check["review_issues"]]))
            if check["missing"]:
                reason += "; Missing marker: " + ", ".join(" / ".join(g) for g in check["missing"])
            extra = groups[strategy].get("functional", {}).get(case["id"])
            detail = f"Supplemental functional check {extra}/5." if extra is not None else ""
            cells.append(f'''<td><details><summary class="{'good' if count == 5 else 'bad'}">{before}/5 → {count}/5</summary>
            <small>Run {sample['repetition'] + 1}: {esc(doc['outcome'])} · {esc(doc['method'])} · {len(doc['text']):,} characters</small>
            <p>{esc(reason or check.get('warning') or 'Original content checks pass.')}</p><p>{esc(detail)}</p>
            <pre>{esc(doc['text'][:1300])}</pre><a href="after/run-{sample['repetition']}-{strategy}.json">Raw run evidence</a></details></td>''')
            if strategy == "adaptive" and failed:
                remaining.append({"id": case["id"], "outcome": doc["outcome"], "passes": count,
                                  "reason": reason.lstrip("; "), "functional_passes": extra,
                                  "sample_repetition": sample["repetition"]})
        rows.append(f'''<tr data-improved="{str(improved).lower()}" data-failed="{str(failed).lower()}">
        <th><a href="{esc(case['url'])}">{esc(case['id'])}</a><small>{esc(case['category'])}</small></th>{''.join(cells)}</tr>''')
    comparison.update(improved_sites=improvements, regressed_sites=regressions, remaining=remaining)
    write_json(ROOT / "comparison.json", comparison)
    for source, target in (("benchmarks/real50-review.json", "review-contract.json"),
                           ("benchmarks/technical-review.json", "functional-contract.json"),
                           ("benchmarks/technical-profile.json", "profile.json"),
                           (".audit/technical.tsv", "decision-trail.tsv")):
        shutil.copyfile(source, ROOT / target)
    summary_rows, md_rows, exa_rows, exa_md = [], [], [], []
    for strategy, name in NAMES.items():
        group, before = groups[strategy], previous["results"][strategy]
        low, high = group["range_seconds"]
        summary_rows.append(f'''<tr class="{'selected' if strategy == 'adaptive' else ''}"><th>{name}</th>
        <td>{label(before['reviewed_passes'])}/50 → <b>{label(group['reviewed_passes'])}/50</b></td>
        <td>{group['median_seconds']:.1f}s<small>range {low:.1f} to {high:.1f}s</small></td>
        <td>{statistics.median(group['requests']):g}</td><td>{statistics.median(group['browser_pages']):g}</td></tr>''')
        md_rows.append(f"| {name} | {label(before['reviewed_passes'])}/50 | {label(group['reviewed_passes'])}/50 | "
                       f"{group['median_seconds']:.1f} s ({low:.1f} to {high:.1f}) | "
                       f"{statistics.median(group['requests']):g} | {statistics.median(group['browser_pages']):g} |")
        group = exa_groups[strategy]
        exa_rows.append(f"<tr><th>{name}</th><td>{label(group['reviewed_passes'])}/5</td>"
                        f"<td>{group['median_seconds']:.1f}s</td><td>{statistics.median(group['requests']):g}</td>"
                        f"<td><a href='exa/run-0-{strategy}.json'>Evidence</a></td></tr>")
        exa_md.append(f"| {name} | {label(group['reviewed_passes'])}/5 | {group['median_seconds']:.1f} s | "
                      f"{statistics.median(group['requests']):g} |")
    functional_rows = "".join(f"<tr><th>{key}</th>" + "".join(f"<td>{groups[s]['functional'][key]}/5</td>" for s in NAMES) + "</tr>"
                              for key in groups["adaptive"]["functional"])
    functional_md = "\n".join(f"| {key} | " + " | ".join(f"{groups[s]['functional'][key]}/5" for s in NAMES) + " |"
                               for key in groups["adaptive"]["functional"])
    exa_cases = json.loads(Path("benchmarks/exa.json").read_text())
    exa_sites_md = "\n".join(f"| {case['id']} | " + " | ".join(
        f"{exa_groups[s]['per_site'][case['id']]}/5" for s in NAMES) + " |" for case in exa_cases)
    exa_sites_html = "".join(f"<tr><th><a href='{esc(case['url'])}'>{esc(case['id'])}</a></th>" + "".join(
        f"<td>{exa_groups[s]['per_site'][case['id']]}/5</td>" for s in NAMES) + "</tr>" for case in exa_cases)
    first_round = json.loads(Path("reports/technical/comparison.json").read_text())
    comparison["first_round"] = {"current": first_round["current"], "exa": first_round["exa"],
                                 "runtime_sha256": first_round["runtime_sha256"]}
    write_json(ROOT / "comparison.json", comparison)
    with (ROOT / "results.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["suite", "site", "strategy", "passing_repetitions", "repetitions", "url"])
        for suite, suite_cases, results in (("same-50", cases, groups), ("exa", exa_cases, exa_groups)):
            for case in suite_cases:
                for strategy in NAMES:
                    writer.writerow([suite, case["id"], strategy, results[strategy]["per_site"][case["id"]],
                                     5, case["url"]])
    exa_explanation = """The first technical round exposed new extraction failures. About returned one 454-character staff biography from a 10,955-character cleaned document. Pricing returned a 522-character enterprise section from 4,880 characters. Both failed all three strategies. Browser-first also chose a 796-character demo fragment on the homepage. HTTP and adaptive passed 3/5 pages; browser-first passed 2/5, consistently across five repetitions. The saved HTML contained the missing content. The shared parser mistook an isolated article section for complete coverage. It now considers the cleaned whole page when there are multiple independent article sections and no main container; an existing main container retains precedence. The final scores reported here come from fresh live runs after that general repair, with the original Exa checks unchanged. Exa is now a development set, not an unseen test. These remain bounded content checks, not proof of complete-page extraction."""
    failures_md = "\n".join(f"| {f['id']} | {f['outcome']} | {f['passes']}/5 | {f['reason']} |" for f in remaining)
    explanation = """Archive and OpenStax needed more time to finish their politely paced JavaScript dependencies. CSS is now loaded because application loaders can wait for it. Browser attempts have a separate 90-second budget; individual HTTP requests still time out after 15 seconds in this comparison. The settling window now controls how long a JavaScript page must stay stable.

edX sends more than 4 MB of decoded HTML. The bounded response limit is now 16 MB, shared by all strategies. Explicit lower byte limits still work.

Windy required three explicitly configured asset origins: node.windy.com, account.windy.com, and ims.windy.com. All use the guarded transport and their robots rules. A robots redirect to an already authorized origin is now supported. Browser requests retain safe Origin and Referer headers for CORS, and HEAD stays HEAD. Cookies and authorization headers are not forwarded.

Playwright's routing layer automatically aborts favicon requests, including Windy's HEAD connectivity check. Chromium CDP Fetch interception now fulfills that request through the guarded transport. A local deny-only proxy blocks native browser egress from any uninstrumented target. No Fetch.continueRequest path is used. The offline banner disappeared and actual forecast values arrived. Map label and basemap tiles remain outside this text-crawling profile, so this does not claim full visual map rendering.

MusicBrainz returned a browser-verification wall during the pilot and was readable again in the first measured run. Verification pages are refused before executing their script. This variation is retained in the results rather than attributed to the parser.

El País was readable in earlier runs but returned HTTP 403 during the final repetition series. Those responses are retained as access refusals. Passing counts can therefore vary across repetitions even with frozen code. A failed example is shown in the site table whenever any repetition fails, rather than hiding it behind a successful first run.

The original Windy check still requires the literal word 'forecast' in body text. The recovered UI exposes temperatures and wind speeds, while that word is in the page title. Its original score is retained as a failure. The separate functional check requires visible weather values, a successful forecast response and HEAD probe, and no offline message. It is a development check, not a replacement for the frozen metric."""
    explanation += """\n\nThe actual CLI also exposed a queueing bug: with the default two browser slots, OpenStax lost part of its 90-second budget before acquiring a slot. The attempt deadline now starts after slot acquisition; the total crawl deadline still bounds the run. A regression sends three slow applications through one browser slot. Credible structured-data pages with very short visible titles also finish promptly instead of burning the rendering deadline. Both repairs were made before the final source freeze and complete rerun."""
    limits = """The same 50 URLs and original reviewed requirements are unchanged. This is a development rerun, not a new held-out evaluation. Each strategy ran five times, with rotated strategy and seed order, fresh databases and browsers, four workers, four browser slots, 500 ms minimum delay per origin, a 1,500-request budget, one retry and no model calls. Sitemaps and link traversal are disabled. These cold starts do not measure long-term learned routing or recrawl accuracy. The new resource profile is identical across strategies, but differs from the earlier 4 MB / shorter-browser-budget profile. Before and after timing is therefore not a controlled speedup measurement. Ranges and request counts are reported alongside failures. Local request telemetry is not an independent server receipt.

Nature still enters an out-of-scope identity and cookie flow. IKEA's requested product still redirects to a category page. NHK completes TLS but returns no HTTP/1.1 bytes before timeout; an independent curl probe also failed. Access walls, robots refusals and unavailable robots files remain refusals. No CAPTCHA solving, login, stealth user agent, paid proxy, Exa API call or hosted model was used.

The attempted optimization to cancel all abandoned browser requests before transmission failed its regression and was removed. Queued obsolete requests can still consume budget. Pilot, failed development experiments, source snapshots, response bodies and decision logs are retained. Review was inline under the user's no-delegation instruction; this is not an independent security audit."""
    support = json.loads((ROOT / "support-verification.json").read_text())
    assert support["verified"] and support["runtime_sha256"] == runtime_digest()
    profile = json.loads((ROOT / "profile-measurement.json").read_text())
    validation = f"""All 63 automated tests pass. The final local fixture uses the normal 90-second browser budget: HTTP recovers 15/18 content cases, and browser-first and adaptive recover 18/18. All three pass 4/4 policy checks, with zero forbidden requests at the fixture server and matching request receipts. The actual CLI, using its default two browser slots, exports verified content for Archive, OpenStax, Windy and Exa's homepage.

The first CLI attempt failed on OpenStax because queue time consumed its render budget. The first default-budget fixture run also timed out on structured-only pages; a shorter-budget diagnostic separated that waiting cost from functional correctness. Both failed runs, the diagnostic rerun and the final passing checks are retained. The final fixture passes without the shorter test-only budget.

An additional adaptive run was profiled separately and excluded from every comparison score and timing. The main event-loop thread profile spans {profile['total_seconds']:.1f} seconds; {profile['event_loop_poll_seconds']:.1f} seconds were spent in polling. That includes waiting for network activity, politeness delays, the browser and parser worker threads. It does not aggregate worker-thread CPU time and is not a measurement of network latency alone. See profile-measurement.json and profile-summary.txt for the trace."""
    markdown = f"""# Technical recovery and the same 50-site rerun

750 final page attempts on the unchanged 50-site set, plus 75 final Exa website attempts. Every strategy ran five times. A complete earlier 825-attempt technical round is preserved in `reports/technical`; it revealed the Exa failures and preceded the actual CLI queueing fix. All crawling ran locally with model inference disabled.

| Strategy | Earlier reviewed checks | Current reviewed checks | Median batch time, range | Median requests | Median renders |
| --- | --- | --- | --- | --- | --- |
{chr(10).join(md_rows)}

New original-check passes: {', '.join(improvements) or 'none'}. Fewer passes than the historical study: {', '.join(regressions) or 'none'}.

## Priority-site functional checks

These checks supplement the original results and do not alter their scores.

| Site | HTTP | Browser | Adaptive |
| --- | --- | --- | --- |
{functional_md}

## What changed and what did not work

{explanation}

The favicon behavior is documented in the [Playwright interception test](https://github.com/microsoft/playwright/blob/main/tests/page/page-request-intercept.spec.ts). The replacement uses the [Chromium Fetch protocol](https://chromedevtools.github.io/devtools-protocol/tot/Fetch/). Actual local regression receipts and live diagnostics are retained in `.audit/` and `reports/technical/development/`.

## Exa website tests

Five public pages were selected from links on the homepage before the supplemental runs: homepage, About, Pricing, documentation introduction, and Contents quickstart. These are website extraction tests. They do not test or consume the Exa search API. The final manifest and raw outputs are under `reports/technical-final/exa`.

| Strategy | Pages passing, each run | Median five-page batch | Median requests |
| --- | --- | --- | --- |
{chr(10).join(exa_md)}

| Exa page | HTTP passing repetitions | Browser | Adaptive |
| --- | --- | --- | --- |
{exa_sites_md}

{exa_explanation}

The first-round offline diagnosis is saved in `reports/technical/exa/diagnosis.json`, including body hashes, candidate sizes and the missing markers found in the complete saved document. It makes no network requests. The first round and final live measurements remain separate.

## Remaining original-check failures

| Site | Example failing outcome | Passing repetitions | Reason |
| --- | --- | --- | --- |
{failures_md}

## Limits and evidence

{limits}

## Verification beyond benchmark scores

{validation}

The original 43 tests remain; 20 additional cases cover the new transport, budgets, CORS, HEAD, robots redirects, native egress, settling, access-wall preflight, queueing and extraction coverage. Source snapshots and compressed bodies are verified by `benchmarks.verify_technical`. The machine-readable summary is `comparison.json`; `results.csv` lists every site and strategy.

Runtime SHA-256: `{comparison['runtime_sha256']}`.
50-site manifest SHA-256: `{comparison['manifest_sha256']}`.
Original review SHA-256: `{comparison['review_sha256']}`.

Repeat the comparison with `.venv/bin/python -m benchmarks.real50 --phase after --repetitions 5 --output-root reports/technical-recheck --profile benchmarks/technical-profile.json`. Completed measured results are never overwritten. Rerun Exa with `.venv/bin/python -m benchmarks.exa --output reports/exa-recheck`.

The local interactive report is at http://127.0.0.1:8788/technical-final/.
"""
    (ROOT / "report.md").write_text(markdown)
    Path("docs/technical-recovery.md").write_text(markdown)
    old_html = Path("reports/real50/index.html").read_text()
    css = re.search(r"<style>(.*?)</style>", old_html, re.S).group(1)
    page = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Prism · Technical recovery</title><style>{css}
    pre{{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.5 monospace;max-width:320px;background:#e9e7df;padding:12px}}
    .prose{{max-width:850px}}.note{{overflow-wrap:anywhere}}.toolbar{{display:flex;gap:12px;flex-wrap:wrap;margin:20px 0}}input,select{{font:inherit;padding:10px;background:transparent;border:1px solid #aeb9af}}
    @media(max-width:600px){{header,main,footer{{padding-left:18px;padding-right:18px}}.hero{{display:block}}.hero aside{{margin-top:25px}}}}
    </style></head><body><header><a class="brand" href="../">PRISM</a><a href="report.md">Written report</a></header><main>
    <div class="hero"><div><p class="eyebrow">Same targets · new implementation · local execution</p><h1>The difficult<br>pages, <em>revisited.</em></h1>
    <p class="lede">Archive, OpenStax and Windy now yield substantive content. The original scores, additional functional checks and remaining refusals are all visible below.</p></div>
    <aside><span class="eyebrow">Measured attempts</span><div class="big">750 + 75</div><p>50 websites and 5 Exa pages.<br>Three approaches. Five runs each.<br>No paid APIs or model calls.</p></aside></div>
    <section><span class="eyebrow">The unchanged metric</span><h2>Same 50 websites</h2><div class="table-wrap"><table id="summary"><thead><tr><th>Approach</th><th>Earlier → current</th><th>Median batch time</th><th>Requests</th><th>Renders</th></tr></thead><tbody>{''.join(summary_rows)}</tbody></table></div>
    <p class="note">The new profile allows larger responses, longer rendering and three explicit Windy dependency origins. Timing is observed cost, not a causal before/after speedup. Original content checks stay unchanged.</p></section>
    <section><h2>Did the priority pages work?</h2><div class="table-wrap"><table><thead><tr><th>Functional check</th><th>HTTP</th><th>Browser</th><th>Adaptive</th></tr></thead><tbody>{functional_rows}</tbody></table></div>
    <p class="note">Windy's original keyword check still fails. The supplemental check requires real weather values, a successful forecast request and HEAD probe, and no offline message. It does not claim full visual map rendering.</p></section>
    <section class="prose"><h2>What broke. What changed.</h2>{''.join('<p>'+esc(p)+'</p>' for p in explanation.split(chr(10)*2))}
    <p class="note">Primary references: <a href="https://github.com/microsoft/playwright/blob/main/tests/page/page-request-intercept.spec.ts">Playwright favicon interception test</a> and <a href="https://chromedevtools.github.io/devtools-protocol/tot/Fetch/">Chromium Fetch protocol</a>.</p></section>
    <section><h2>Exa.ai, separately tested</h2><p>Homepage, About, Pricing and two documentation pages. Public website extraction only; no Exa API calls.</p><div class="table-wrap"><table><thead><tr><th>Approach</th><th>Passing pages</th><th>Median batch</th><th>Requests</th><th>Source</th></tr></thead><tbody>{''.join(exa_rows)}</tbody></table></div>
    <div class="table-wrap"><table><thead><tr><th>Page / passing repetitions</th><th>HTTP</th><th>Browser</th><th>Adaptive</th></tr></thead><tbody>{exa_sites_html}</tbody></table></div><p class="prose">{esc(exa_explanation)}</p><p><a href="../technical/exa/diagnosis.json">Inspect the first-round diagnosis</a> · <a href="../technical/">First technical round</a></p></section>
    <section><h2>Inspect all 50 targets</h2><div class="toolbar"><input id="search" placeholder="Search site or category" aria-label="Search sites"><select id="filter" aria-label="Filter sites"><option value="all">All sites</option><option value="improved">Improved scores</option><option value="failed">Remaining failures</option></select><span id="count"></span></div>
    <div class="table-wrap"><table id="sites"><thead><tr><th>Website</th><th>HTTP</th><th>Browser</th><th>Adaptive</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></section>
    <section class="prose"><h2>Limits and evidence</h2>{''.join('<p>'+esc(p)+'</p>' for p in limits.split(chr(10)*2))}
    <h2>Verification beyond the scores</h2>{''.join('<p>'+esc(p)+'</p>' for p in validation.split(chr(10)*2))}
    <p><a href="comparison.json">Computed results</a> · <a href="results.csv">Results CSV</a> · <a href="verification.json">Evidence verification</a> · <a href="support-verification.json">CLI and fixture checks</a> · <a href="decision-trail.tsv">Decision trail</a> · <a href="../real50/">Historical study</a></p>
    <p class="note">Runtime {comparison['runtime_sha256']}<br>Manifest {comparison['manifest_sha256']}</p></section></main><footer>Prism · Free local crawling with explicit limits.</footer>
    <script>const search=document.querySelector('#search'),filter=document.querySelector('#filter'),rows=[...document.querySelectorAll('#sites tbody tr')];
    function update(){{let count=0;for(const row of rows){{const match=row.textContent.toLowerCase().includes(search.value.toLowerCase())&&(filter.value==='all'||row.dataset[filter.value]==='true');row.hidden=!match;if(match)count++;}}document.querySelector('#count').textContent=count+' sites';}}
    search.addEventListener('input',update);filter.addEventListener('change',update);update();</script></body></html>'''
    (ROOT / "index.html").write_text(page)
    print(json.dumps({"groups": groups, "exa": exa_groups, "improved": improvements, "regressed": regressions}, indent=2))


if __name__ == "__main__":
    main()
