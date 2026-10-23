import hashlib
import html
import json
import shutil
import statistics
from collections import Counter
from pathlib import Path

from .real50 import runtime_digest, write_json
from .real50_review import reviewed_checks

ROOT = Path("reports/real50")
NAMES = {"http": "HTTP only", "browser": "Browser first", "adaptive": "Adaptive"}


def esc(value):
    return html.escape(str(value), quote=True)


def load_phase(phase):
    metadata = json.loads((ROOT / phase / "phase.json").read_text())
    records = [json.loads((ROOT / phase / f"run-{i}-{strategy}.json").read_text())
               for i in range(5) for strategy in NAMES]
    for record in records:
        record["reviewed"] = reviewed_checks(record)
    groups = {}
    for strategy in NAMES:
        runs = [r for r in records if r["strategy"] == strategy]
        durations = [r["summary"]["elapsed_ms"] / 1000 for r in runs]
        groups[strategy] = {
            "reviewed_passes": [sum(c["passed"] for c in r["reviewed"]) for r in runs],
            "topic_passes": [sum(c["passed"] for c in r["checks"]) for r in runs],
            "claimed_ok": [sum(d["outcome"] == "ok" for d in r["documents"]) for r in runs],
            "median_seconds": statistics.median(durations),
            "range_seconds": [min(durations), max(durations)],
            "requests": [r["summary"]["requests"] for r in runs],
            "browser_pages": [r["summary"]["browser_pages"] for r in runs],
            "bytes": [r["summary"]["bytes"] for r in runs],
            "outcomes": dict(Counter(d["outcome"] for r in runs for d in r["documents"])),
            "per_site": {c["id"]: sum(next(x for x in r["reviewed"] if x["id"] == c["id"])["passed"]
                                      for r in runs) for c in runs[0]["reviewed"]},
        }
    return {"metadata": metadata, "records": records, "groups": groups}


def count_label(values):
    return str(values[0]) if len(set(values)) == 1 else f"{min(values)}–{max(values)}"


def main():
    before, after = load_phase("baseline"), load_phase("after")
    assert before["metadata"]["manifest_sha256"] == after["metadata"]["manifest_sha256"]
    assert before["metadata"]["harness_sha256"] == after["metadata"]["harness_sha256"]
    assert after["metadata"]["runtime_sha256"] == runtime_digest()
    cases = json.loads(Path("benchmarks/real50.json").read_text())
    profile = json.loads((ROOT / "profile.json").read_text())
    comparison = {
        "kind": "50 new sites, 5 runs per strategy before and after general fixes",
        "scope": "One frozen URL per website; no link traversal or site-wide completeness claim",
        "manifest_sha256": before["metadata"]["manifest_sha256"],
        "review_contract_sha256": hashlib.sha256(Path("benchmarks/real50-review.json").read_bytes()).hexdigest(),
        "baseline": {"metadata": before["metadata"], "results": before["groups"]},
        "after": {"metadata": after["metadata"], "results": after["groups"]},
    }
    write_json(ROOT / "comparison.json", comparison)
    shutil.copyfile("benchmarks/real50-review.json", ROOT / "review-contract.json")
    shutil.copyfile(".audit/real50.tsv", ROOT / "decision-trail.tsv")

    table, markdown_rows = [], []
    for strategy, name in NAMES.items():
        a, b = before["groups"][strategy], after["groups"][strategy]
        table.append(f"""<tr class="{'selected' if strategy == 'adaptive' else ''}"><th>{name}</th>
        <td>{count_label(a['reviewed_passes'])}/50 → <strong>{count_label(b['reviewed_passes'])}/50</strong></td>
        <td>{a['median_seconds']:.1f}s → {b['median_seconds']:.1f}s
        <small>before {a['range_seconds'][0]:.1f}–{a['range_seconds'][1]:.1f}s<br>
        after {b['range_seconds'][0]:.1f}–{b['range_seconds'][1]:.1f}s</small></td>
        <td>{statistics.median(a['requests']):g} → {statistics.median(b['requests']):g}</td>
        <td>{statistics.median(a['browser_pages']):g} → {statistics.median(b['browser_pages']):g}</td></tr>""")
        markdown_rows.append(f"| {name} | {count_label(a['reviewed_passes'])}/50 | {count_label(b['reviewed_passes'])}/50 | "
                             f"{a['median_seconds']:.2f} → {b['median_seconds']:.2f} s | "
                             f"{statistics.median(a['requests']):g} → {statistics.median(b['requests']):g} | "
                             f"{statistics.median(a['browser_pages']):g} → {statistics.median(b['browser_pages']):g} |")

    rows, failures, recovered, regressions = [], [], [], []
    for case in cases:
        cells = []
        improved = any(after["groups"][s]["per_site"][case["id"]] > before["groups"][s]["per_site"][case["id"]]
                       for s in NAMES)
        regressed = any(after["groups"][s]["per_site"][case["id"]] < before["groups"][s]["per_site"][case["id"]]
                       for s in NAMES)
        remaining = after["groups"]["adaptive"]["per_site"][case["id"]] < 5
        if improved:
            recovered.append(case["id"])
        if regressed:
            regressions.append(case["id"])
        for strategy in NAMES:
            old = before["groups"][strategy]["per_site"][case["id"]]
            new = after["groups"][strategy]["per_site"][case["id"]]
            record = next(r for r in after["records"] if r["repetition"] == 0 and r["strategy"] == strategy)
            old_record = next(r for r in before["records"] if r["repetition"] == 0 and r["strategy"] == strategy)
            check = next(c for c in record["reviewed"] if c["id"] == case["id"])
            document = next(d for d in record["documents"] if d["url"] == check["url"])
            old_document = next(d for d in old_record["documents"] if d["url"] == check["url"])
            problem = "; ".join(filter(None, [check["reason"], *check["review_issues"]]))
            if not check["topic_passed"] and check["outcome"] == "ok":
                problem = "Topic or length check failed. " + problem
            cells.append(f"""<td><details><summary class="{'good' if new == 5 else 'bad'}">{old}/5 → {new}/5</summary>
            <small>Before: {esc(old_document['outcome'])} · {esc(old_document['parser'])}</small>
            <pre class="excerpt">{esc(old_document['text'][:450] or old_document['reason'])}</pre>
            <small>After: {esc(check['outcome'])} · {esc(check['method'])} · {esc(check['parser'])}</small>
            <p>{esc(problem or check['warning'] or 'Required content is present.')}</p>
            <pre class="excerpt">{esc(document['text'][:950])}</pre>
            <a href="baseline/run-0-{strategy}.json">Before evidence ↗</a> ·
            <a href="after/run-0-{strategy}.json">After evidence ↗</a></details></td>""")
            if strategy == "adaptive" and remaining:
                failures.append({"id": case["id"], "url": case["url"], "outcome": check["outcome"],
                                 "reason": problem or check["failure"], "passes": new})
        rows.append(f"""<tr data-improved="{str(improved).lower()}" data-failed="{str(remaining).lower()}">
        <th><a href="{esc(case['url'])}" target="_blank" rel="noreferrer">{esc(case['id'])} ↗</a>
        <small>{esc(case['category'])}</small></th>{''.join(cells)}</tr>""")
    comparison.update({"improved_sites": recovered, "regressed_sites": regressions,
                       "remaining_adaptive_failures": failures})
    write_json(ROOT / "comparison.json", comparison)
    a, b = before["groups"]["adaptive"], after["groups"]["adaptive"]
    failure_lines = "\n".join(f"| {f['id']} | {f['outcome']} | {f['passes']}/5 | {f['reason']} |" for f in failures)
    fixes = [
        ("WHO", "Valid security headers exceeded the HTTP client's 8 KiB field limit. The limit is now bounded at 64 KiB; certificate checks and robots policy remain active."),
        ("NHS and Coursera", "An isolated FAQ answer or metadata description outranked the complete page. Longer credible document candidates now outrank those fragments."),
        ("React", "Serialized component state was saved as prose. Encoded JSON containers are unpacked or skipped so readable source content can win."),
        ("MathWorld", "Image-based equations disappeared. Equation and MathML alternative text now survives extraction."),
        ("Go tour", "The renderer stopped while application dependencies were still loading. Empty apps now use the existing total page budget; static content can finish early."),
        ("Failure reporting and shutdown", "Challenge, waiting-room and offline error pages are no longer counted as content. Route completion survives cancellation, and accessible open shadow roots can be serialized."),
    ]
    fix_html = "".join(f"<li><b>{title}.</b> {esc(text)}</li>" for title, text in fixes)
    markdown = f"""# Fifty-site real-web comparison

All three strategies were tested on one URL from each of 50 websites not used to tune the initial build. Five interleaved runs per strategy were captured before general fixes, followed by five after them. All 1,500 page attempts remain in the evidence. No model inference or paid crawling service was used.

| Strategy | Before, reviewed checks | After, reviewed checks | Median elapsed before → after | Requests, median | Browser pages, median |
| --- | --- | --- | --- | --- | --- |
{chr(10).join(markdown_rows)}

These checks require topic coverage and minimum content, plus four additional requirements found during inline inspection of the first baseline outputs. The original topic checks are preserved unchanged. Their counts are in `comparison.json`. The additional checks reject serialized React state, a missing mathematical equation, a truncated course description, and a product redirect to a category page. All checks apply identically to before and after outputs.

Improved sites: {', '.join(recovered)}. Sites with fewer passing repetitions after the fixes: {', '.join(regressions) or 'none'}.

## What changed

{chr(10).join(f'- **{title}.** {text}' for title, text in fixes)}

Ten new local regressions reproduce these failure classes. The full suite has 43 passing tests. Source snapshots, request records, response captures, extracted documents and failed development attempts are retained. The implementation was reviewed inline; no independent review or production deployment was performed.

## Run a page the baseline missed

```sh
cd /Users/aditya/Me/optimal-crawler
.venv/bin/prism crawl https://go.dev/tour/welcome/1 --max-pages 1 --no-discover --no-follow-links --db data/tour.db
.venv/bin/prism export --db data/tour.db --output data/tour.jsonl
```

The actual CLI recheck saved 3,295 characters from this JavaScript page with outcome `ok` and method `browser`. Its retained output is `data/real50-demo.jsonl` and the command log is `.audit/real50-demo.log`.

## What still fails

| Site | Outcome in first after-run | Passing repetitions | Evidence or reason |
| --- | --- | --- | --- |
{failure_lines}

Robots refusals and access restrictions remain in the 50-site denominator, but are listed separately from extraction failures. The unavailable IKEA product is not replaced with an easier URL. Some successful outputs retain navigation, editorial notes or bibliography. Passing these checks is not proof of complete or perfectly clean extraction.

## Measurement limits

Each run uses a fresh database and browser. Four workers, four browser slots, a 500 ms minimum per-origin delay, a 15-second request timeout, a 3-second rendering allowance, a 4 MB response limit and 1,500-request budget are shared across strategies and phases. The browser's total deadline is unchanged. Sitemaps and link traversal are disabled to isolate recovery of the same 50 URLs. Models are disabled.

The before and after phases ran at different times. Strategies rotate order within each phase, but public sites, networks and caches can still change. The runtime ranges and all five values are retained. Treat duration as an observed cost, not a controlled causal speedup. Request totals come from local crawler telemetry, not independent receipts from the public servers. Bytes measure retained decoded bodies, not wire traffic.

A separate profiled adaptive run took {profile['total_profiled_seconds']:.2f} seconds, with {profile['event_loop_poll_seconds']:.2f} seconds in event-loop polling. It is excluded from the comparison. The measured path is dominated by waiting for network activity and deliberate pacing, rather than CPU saturation. The before/after duration ranges overlap for each strategy; these fixes establish a recovery improvement, not a measurable speed improvement.

This set was new before the baseline. It became a development set when its failures informed fixes. The rerun does not establish accuracy on a new untouched set, and the sample is hand-selected rather than random. English dominates; French, Spanish, German and Japanese targets are also included. No claim of universal website support is made.

Manifest SHA-256: `{comparison['manifest_sha256']}`.
Before runtime SHA-256: `{before['metadata']['runtime_sha256']}`.
After runtime SHA-256: `{after['metadata']['runtime_sha256']}`.

Completed phases reject mixed source versions and preserve completed run files. The exact measurement script is saved beside each phase. To repeat the current implementation, run `.venv/bin/python -m benchmarks.real50 --phase after --repetitions 5 --output-root reports/real50-recheck`. The new output directory keeps these original results intact. The current runner records the imported package path; the measured phases used the saved earlier harness with the same scoring and execution settings.
"""
    (ROOT / "report.md").write_text(markdown)
    Path("docs/real50.md").write_text(markdown)
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Prism · 50 real websites</title>
<style>
:root{{--paper:#f3f0e8;--ink:#192622;--muted:#5f6d67;--line:#cdd2c9;--accent:#df5c31;--green:#21694c}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.6 'Avenir Next',Arial,sans-serif}}
header,main,footer{{max-width:1180px;margin:auto;padding:0 32px}}header{{padding-top:26px;padding-bottom:20px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between}}
a{{color:inherit;text-underline-offset:4px}}a:hover{{color:var(--accent)}}.brand{{letter-spacing:5px;font-weight:800}}.brand:before{{content:'◩';color:var(--accent);margin-right:12px}}
.hero{{padding:65px 0 44px;display:grid;grid-template-columns:1.5fr 1fr;gap:50px}}h1{{font:clamp(42px,5.6vw,72px)/1.08 Georgia,serif;letter-spacing:-2px;margin:20px 0}}h1 em{{font-style:normal;color:var(--accent)}}
.eyebrow{{font:12px monospace;letter-spacing:2px;text-transform:uppercase;color:var(--muted)}}.lede,.note{{color:var(--muted)}}.note{{font-size:13px}}.hero aside{{align-self:center;border-left:2px solid var(--accent);padding:24px}}
.big{{font:64px Georgia,serif;letter-spacing:-2px;margin:12px 0}}section{{padding:36px 0;border-top:1px solid var(--line)}}h2{{font:32px/1.2 Georgia,serif;margin:12px 0 24px}}
.table-wrap{{overflow:auto}}table{{border-collapse:collapse;width:100%;font-size:14px;text-align:left}}th,td{{border-bottom:1px solid var(--line);padding:17px 14px;vertical-align:top}}th:first-child{{padding-left:0}}thead{{font-size:11px;text-transform:uppercase;letter-spacing:1px;color:var(--muted)}}tbody th{{font-weight:600}}
small{{display:block;color:var(--muted);font-size:11px;margin-top:5px}}.selected{{background:#e5ebdf}}.selected th{{padding-left:10px}}strong,.good{{color:var(--green)}}.bad{{color:#9c3e25}}
.filters{{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:20px}}input,button{{font:inherit;padding:10px 14px;border:1px solid #9aa79d;background:transparent;max-width:100%}}button{{cursor:pointer}}button.active{{background:var(--ink);color:var(--paper)}}:focus-visible{{outline:3px solid var(--accent);outline-offset:3px}}
summary{{cursor:pointer;font-weight:600}}details p{{font-size:12px;max-width:260px}}.excerpt{{max-width:270px;max-height:240px;overflow:auto;font:12px/1.5 monospace;white-space:pre-wrap}}details a{{font-size:12px}}.command{{padding:22px;background:var(--ink);color:var(--paper);font:13px/1.8 monospace;white-space:pre-wrap;overflow:auto}}
li{{margin:15px 0}}.callout{{padding:18px 24px;background:#eae5da;border-left:3px solid var(--accent)}}.links{{display:flex;gap:20px;flex-wrap:wrap}}code{{overflow-wrap:anywhere}}footer{{padding-top:24px;padding-bottom:40px;font-size:12px;color:var(--muted)}}[hidden]{{display:none!important}}
@media(max-width:760px){{header,main,footer{{padding-left:20px;padding-right:20px}}.hero{{grid-template-columns:1fr;gap:10px;padding-top:40px}}.hero aside{{padding:16px 24px}}table{{min-width:680px}}header>a{{font-size:12px}}}}
</style></head><body><header><span class="brand">PRISM</span><a href="report.md">Full experiment notes ↗</a></header>
<main><div class="hero"><div><p class="eyebrow">Project 02 / New-site evaluation / 23 October 2026</p>
<h1>Fifty websites.<br><em>Before and after.</em></h1><p class="lede">Three strategies. Ten categories. One frozen URL per site. Five runs per strategy before fixes and five after, with every refusal and failed extraction retained.</p>
<p class="eyebrow">1,500 page attempts · local execution · no model fees</p></div>
<aside><p class="eyebrow">Adaptive / reviewed content checks</p><div class="big">{count_label(a['reviewed_passes'])} → {count_label(b['reviewed_passes'])}<span style="font-size:25px"> / 50</span></div>
<p>{len(recovered)} sites improved in at least one strategy. {len(regressions)} sites lost passing repetitions. These are bounded content checks, not complete-page accuracy.</p></aside></div>
<section><p class="eyebrow">01 / The comparison</p><h2>Recovery and its actual cost.</h2><div class="table-wrap"><table>
<thead><tr><th>Strategy</th><th>Reviewed checks<br>before → after</th><th>Median elapsed</th><th>Requests, median</th><th>Browser pages, median</th></tr></thead><tbody>{''.join(table)}</tbody></table></div>
<p class="note">Every cell summarizes five runs. Request counts include robots, retries and allowed assets. Elapsed times are observations; changing networks, caches and website content prevent a controlled causal speed claim across phases.</p>
<div class="callout">The original keyword checks missed four misleading outputs. Separate review checks reject serialized UI data, a missing equation, a truncated course overview and a redirect to the wrong product category. Both scoring versions are preserved.</div></section>
<section><p class="eyebrow">02 / What broke and what changed</p><h2>General fixes, tested against the failures.</h2><ol>{fix_html}</ol>
<p class="note">43 automated tests pass, including ten new regressions. Archive and OpenStax still failed in the development recheck under the same rendering budget. Challenge pages are classified and refused. Scope, robots rules and cost limits were not relaxed.</p></section>
<section><p class="eyebrow">03 / All fifty sites</p><h2>Inspect every result.</h2>
<p class="note">Each cell shows passing repetitions out of five, before → after. Expand a cell for an actual after-run excerpt, method and failure reason. Refused sites stay in the denominator.</p>
<div class="filters"><input id="search" type="search" placeholder="Find a site or category" aria-label="Filter websites"><button data-mode="all" class="active">All sites</button><button data-mode="improved">Improved</button><button data-mode="failed">Still fails adaptive</button><span id="count" aria-live="polite"></span></div>
<div class="table-wrap"><table id="sites"><thead><tr><th>Website</th><th>HTTP only</th><th>Browser first</th><th>Adaptive</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></section>
<section><p class="eyebrow">04 / Boundaries of the evidence</p><h2>The first pass was new. The rerun is a development result.</h2>
<p>The sites were not used to tune the first build. Their failures then informed the fixes, so the second phase is no longer an untouched evaluation. These 50 sites are hand-selected and English-heavy. One page per website does not measure full-site discovery or completeness.</p>
<p>Each run uses fresh storage and a fresh browser, four workers, 500 ms minimum per-origin pacing, 15-second requests, the same total rendering deadline, a 4 MB response limit and a 1,500-request budget. Models, link following and sitemap discovery are disabled for this comparison.</p>
<p>A separate profiled adaptive run spent {profile['event_loop_poll_seconds']:.2f} of {profile['total_profiled_seconds']:.2f} seconds in event-loop polling. Waiting on network activity and pacing dominates this workload. The before/after timing ranges overlap; recovery improved, but a speed improvement is not established. <a href="profile-summary.txt">Inspect the excluded profile ↗</a></p>
<p>Some usable outputs retain navigation or bibliography. Authentication flows, denied robots rules, anti-bot walls, unavailable products, large responses and apps requiring more time or dependencies remain explicit limits. No third-party service was purchased, and no independent review was performed.</p>
<div class="links"><a href="comparison.json">Computed comparison ↗</a><a href="baseline/summary.json">Original runs ↗</a><a href="after/summary.json">Fixed runs ↗</a><a href="review-contract.json">Additional review checks ↗</a><a href="decision-trail.tsv">Decision trail ↗</a></div>
<p class="note">Frozen manifest SHA-256: <code>{comparison['manifest_sha256']}</code><br>Before source: <code>{before['metadata']['runtime_sha256']}</code><br>After source: <code>{after['metadata']['runtime_sha256']}</code></p></section>
<section><p class="eyebrow">05 / Run the fixed version</p><h2>Try a page the baseline missed.</h2>
<pre class="command">cd /Users/aditya/Me/optimal-crawler
.venv/bin/prism crawl https://go.dev/tour/welcome/1 --max-pages 1 --no-discover --no-follow-links --db data/tour.db
.venv/bin/prism export --db data/tour.db --output data/tour.jsonl</pre>
<p>The real CLI recheck recovered 3,295 characters with browser fallback. <a href="demo.jsonl">Inspect its saved output ↗</a></p></section></main>
<footer>PRISM / Raw outputs, response captures, original source and current source are retained locally.</footer>
<script>const rows=[...document.querySelectorAll('#sites tbody tr')],search=document.getElementById('search');let mode='all';function filter(){{let count=0;for(const row of rows){{const show=row.querySelector('th').textContent.toLowerCase().includes(search.value.toLowerCase())&&(mode==='all'||row.dataset[mode]==='true');row.hidden=!show;if(show)count++}}document.getElementById('count').textContent=count+' sites'}}search.addEventListener('input',filter);document.querySelectorAll('[data-mode]').forEach(button=>button.addEventListener('click',()=>{{mode=button.dataset.mode;document.querySelectorAll('[data-mode]').forEach(other=>other.classList.toggle('active',other===button));filter()}}));filter();</script>
</body></html>"""
    (ROOT / "index.html").write_text(page)
    print(ROOT / "index.html")


if __name__ == "__main__":
    main()
