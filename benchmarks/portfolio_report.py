"""Build a compact portfolio from saved receipts; never fetches a website."""
import argparse
import csv
import difflib
import hashlib
import html
import json
import shutil
from pathlib import Path

from benchmarks.provider_live_report import distribution


def read(path):
    return json.loads(path.read_text())


def build(source: Path, output: Path):
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    for phase in ('initial-first', 'reserved-first'):
        for row in read(source / phase / 'summary.json')['rows']:
            receipt = read(source / phase / row['id'] / 'result.json')
            rows.append({**row, 'cohort': receipt['case']['cohort'], 'url': receipt['case']['url'],
                         'method': receipt['document'].get('method'),
                         'characters': len(receipt['document']['text'])})
    passed = sum(r['passed'] and not r['skipped_cloudflare'] for r in rows)
    skipped = sum(r['skipped_cloudflare'] for r in rows)
    assert len(rows) == 50
    load = {name: read(source / name / 'summary.json') for name in
            ('load-before', 'load-after', 'load-mixed', 'load-final', 'load-sustained')}
    for name in ('load-after', 'load-mixed', 'load-final', 'load-sustained'):
        assert all(r['correct'] for r in load[name]), f'Failed {name}; do not hide it'
    sustained = load['load-sustained'][0]
    original = read(source / 'freeze.json')
    phases = {name: read(source / name / 'phase.json') for name in
              ('initial-first', 'reserved-first', 'load-before', 'load-after', 'load-final', 'load-sustained')}
    summary = {'first_exposure': {'total': len(rows), 'passed': passed,
               'cloudflare_skips': skipped, 'non_cloudflare_total': len(rows)-skipped,
               'all_site_rate': passed/len(rows), 'non_cloudflare_rate': passed/(len(rows)-skipped),
               'all_cases_latency': distribution([r['elapsed_ms'] for r in rows]),
               'passing_cases_latency': distribution([r['elapsed_ms'] for r in rows if r['passed']])},
               'rows': rows, 'freeze': original, 'phases': phases, 'load': load,
               'audit': read(source / 'content-audit.json'),
               'focused_rechecks': {name: read(source / name / 'summary.json') for name in
                                    ('content-recheck', 'redirect-recheck')},
               'validation': (source / 'tests-final.txt').read_text().strip().splitlines()[-1]}
    (output / 'summary.json').write_text(json.dumps(summary, indent=2))
    shutil.copyfile('benchmarks/holdout50.json', output / 'manifest.json')
    shutil.copyfile(source / 'content-audit.json', output / 'content-audit.json')
    shutil.copyfile(source / 'content-replay.json', output / 'content-replay.json')
    with (output / 'sites.csv').open('w') as file:
        fields = ['id', 'cohort', 'url', 'passed', 'skipped_cloudflare', 'outcome', 'status',
                  'method', 'characters', 'elapsed_ms', 'reason']
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction='ignore', lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)
    patch = []
    for name in ('store.py', 'crawler.py', 'extract.py', 'verification.py', 'network.py'):
        before = source / 'initial-first' / 'source' / name
        after = Path('src/optimal_crawler') / name
        patch.extend(difflib.unified_diff(before.read_text().splitlines(True), after.read_text().splitlines(True),
                     fromfile='before/'+name, tofile='after/'+name))
    (output / 'fixes.patch').write_text(''.join(patch))
    shutil.copyfile(source / 'demo' / 'demo.webm', output / 'demo.webm')
    shutil.copyfile(source / 'demo' / 'demo.png', output / 'demo.png')
    shutil.copyfile(source / 'demo' / 'status.json', output / 'demo-status.json')
    # Source snapshots establish what ran without distributing captured website bodies.
    for name in ('initial-first', 'reserved-first'):
        shutil.copytree(source / name / 'source', output / 'source-snapshots' / name,
                        dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree('src/optimal_crawler', output / 'source-snapshots' / 'final',
                    dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__'))
    rate = 100*passed/(len(rows)-skipped)
    latency = summary['first_exposure']['passing_cases_latency']
    technical = load['load-after'][2]
    report = f'''# Prism: measured results and engineering assessment

Measured locally on an Apple M4 Pro, Python 3.13. Dates and source hashes are in [the evidence](../examples/evidence/summary.json). No vendor API calls or paid inference were used in this round.

## New-site evaluation

The manifest froze 50 distinct hostnames not in the earlier development manifest, before any target fetch. This was a purposeful varied sample, not a random sample of the web. Forty sites were tested first; ten stayed unrequested until the initial fixes and 146-test suite passed. Each site used a fresh database, robots enforcement, a 55-second run budget and frozen lexical expectations. Confirmed Cloudflare challenges were skipped without retries or replacement URLs.

| Cohort | Strict passes | Cloudflare skipped | Passes excluding Cloudflare |
|---|---:|---:|---:|
| Initial first exposure | 34/40 | 4 | 34/36 (94.4%) |
| Reserved first exposure | 5/10 | 0 | 5/10 (50.0%) |
| Combined first exposures | {passed}/50 | {skipped} | {passed}/{len(rows)-skipped} ({rate:.1f}%) |

Combined observations span two source revisions; they are not a single final-version run. The reserved ten are the cleanest estimate after the initial fixes, and their 5/10 result matters. Once inspected, both cohorts are development data. We did not rewrite the criteria or replace failed URLs.

Passing URLs took median {latency['median_ms']/1000:.2f}s and p95 {latency['p95_ms']/1000:.2f}s end to end, including crawler setup and teardown. First-cohort timing was not an isolated performance test; tooling work ran alongside part of it. Four Cloudflare exclusions were Europeana, MoMA, OECD and Stack Overflow. The original per-case `started_at` field was mistakenly populated at completion; phase start/finish and measured monotonic durations are authoritative. The reserved harness corrects that field.

## Every remaining non-Cloudflare failure

| Case | First observation | Follow-up |
|---|---|---|
| Rijksmuseum | 290-character valid about page lacks frozen “art” keyword | Source review confirms extraction; strict failure retained |
| Louvre | Actual HTTP 404 | Preserved as error; no substituted URL |
| Signal | Actual HTTP 404 | Preserved as error; no substituted URL |
| Japan Meteorological Agency | Actual HTTP 404 | Preserved as error; no substituted URL |
| gov.br | Actual HTTP 404 | Preserved as error; no substituted URL |
| GIMP | Robots redirects HTTPS → HTTP outside authorized origins | Explicit observed helper authorization succeeds in 5.06s; configuration-dependent development recheck |
| OBS | Server repeatedly returns 302 to the same URL | Loop detector cuts page requests from six to one, 4.70s → 1.14s; still no content |

Four dead URLs expose a weakness in the benchmark's URL selection. They also reflect a real crawler responsibility: return an accurate failure, not unrelated content. They remain in the denominator.

## Content quality: keyword success was insufficient

A 12-case source/output spot review found two false assurances inside the initial passing set. ESA lost card headings because all HTML headers were removed. The fix preserves headings inside section/card headers; the verifier now checks them too. World Bank exposed undecorated CMS layout tables. A narrowly detected CMS prose candidate removes grid cells and section metadata while retaining the narrative. Fresh focused requests pass for both; replaying all 35 initially accepted source captures produces no new rejections. This replay is a development regression check, not another live benchmark.

The [source audit](../examples/evidence/content-audit.json) records limitations: PostgreSQL and VideoLAN retain boilerplate, World Bank retains some navigation/configuration labels, and the long PNG specification exceeds full-text verification bounds. The audit is purposeful, performed during development, and is not a blind human-labelled precision/recall study. The 94.4% initial keyword result must not be advertised as 94.4% extraction accuracy.

## Crash recovery: measured failure, root cause, fix

The harness sends actual SIGKILL to a crawler subprocess and resumes the same database. An origin-side ledger independently checks what was requested. It expects exact URL sets, first/last text markers, unique exported records, valid SQLite integrity and zero requests to a robots-denied endpoint.

| Experiment, 808 expected useful documents | Before | After atomic commit |
|---|---:|---:|
| Kill during in-flight requests | 808/808 | 808/808 |
| Kill immediately after recording the first page | **708/808** | **808/808** |

Previously the root was marked done before its 100 discovered children were enqueued. Resume correctly skipped the completed root and therefore never discovered those children. Page results, duplicate state, frontier completion and discovered child rows now commit together. A failed child insert rolls back completion. Two behavioral regressions cover durable children and transaction rollback. Interrupted network requests may repeat; this is not exactly-once network delivery or a power-loss durability proof.

## Local load results

| Workload | Useful documents | Wall time | Throughput | Peak sampled crawler-tree RSS |
|---|---:|---:|---:|---:|
| Short HTTP trial, 8 origins / 8 workers, 15ms fixture delay | 808/808 | {technical['wall_s']:.2f}s | {technical['useful_documents_per_s']:.1f}/s | {technical['peak_sampled_process_tree_rss_bytes']/2**20:.1f} MiB |
| Sustained final-source HTTP trial, 8 origins / 8 workers, 60ms delay | {sustained['ok']}/{sustained['expected_ok']} | {sustained['wall_s']:.2f}s | {sustained['useful_documents_per_s']:.1f}/s | {sustained['peak_sampled_process_tree_rss_bytes']/2**20:.1f} MiB |

The short HTTP matrix after the atomic fix measured 37.0, 153.6 and 291.7 useful documents/s at 1, 4 and 8 workers. These are single trials, not confidence intervals or a proven speed improvement. Final-source mixed trials additionally exercise actual Chromium rendering, first-request 503s and both crash windows; all expected documents must be recovered to generate this report.

The sustained trial's successful-page p95 is {sustained['successful_page_latency']['p95_ms']:.1f}ms; worker CPU time is {sustained['cpu_self_s']:.2f}s. RSS samples every 250ms include crawler child processes and exclude the fixture server. CPU for recovery trials covers only the resumed process. Parent wall time includes process startup, monitoring and restart. Per-page latency excludes waiting in the frontier. The same machine hosts the source servers, whose configured delay bounds throughput. Documents have distinct markers but deliberately similar prose, exercising near-duplicate indexing. No deduplicated record is dropped from correctness accounting.

This is an approximately minute-scale, 8k-document local test, not an endurance study, internet throughput claim, distributed benchmark or evidence for millions of pages/day. Disk exhaustion, machine power loss and multi-host failover remain untested.

## Reproduce and review

See [README commands](../README.md), [source diff](../examples/evidence/fixes.patch), [per-site CSV](../examples/evidence/sites.csv) and [machine-readable receipts](../examples/evidence/summary.json). The short demo records an actual local mixed crawl and two real crash/restart trials; it makes no external network requests. The video and summarized evidence ship in `examples/evidence/`. Raw public-page bodies and local account files do not.

**Assessment:** worth showing as a transparent backend engineering project: adaptive retrieval, bounded networking, crash-safe frontier transactions, reproducible failure diagnosis and source-aware evaluation. It does not establish Exa/Firecrawl parity or universal coverage. The strongest story is the data-loss reproduction and fix, followed by the content-audit corrections—not a single accuracy percentage.
'''
    Path('docs/evaluation.md').write_text(report)
    rows_html = ''.join('<tr>'+''.join('<td>'+html.escape(str(value))+'</td>' for value in
                       (r['id'], r['cohort'], 'Cloudflare skipped' if r['skipped_cloudflare'] else
                        'Pass' if r['passed'] else r['reason'] or 'Keyword mismatch',
                        f"{r['elapsed_ms']/1000:.2f}s", r['characters']))+'</tr>' for r in rows)
    (output / 'index.html').write_text(f'''<!doctype html><html lang="en"><meta charset="utf-8"><title>Prism · evidence</title>
<style>body{{background:#f3f5ef;color:#142a25;font:17px system-ui;max-width:1100px;margin:50px auto;padding:0 25px;line-height:1.55}}h1{{font-size:58px;letter-spacing:-2px;line-height:1.1}}h2{{margin-top:45px}}a{{color:#246651}}.stats{{display:flex;gap:20px;flex-wrap:wrap}}.stats div{{background:white;border:1px solid #d8ded4;padding:24px;flex:1;min-width:170px}}strong.number{{font-size:36px;display:block}}video{{width:100%;background:#101b22;border-radius:10px}}table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{text-align:left;padding:12px;border-bottom:1px solid #ccd5ca}}.note{{border-left:4px solid #6b8a76;padding:12px 22px;background:#e7ede3}}small{{color:#63726a}}code{{background:#e3e9df;padding:3px}}</style>
<small>PRISM / LOCAL ENGINEERING EVIDENCE / OCTOBER 2026</small><h1>A crawler tested against<br>its own failure modes.</h1>
<p>Frozen new-site evaluation, exact-content load fixtures, and real SIGKILL recovery. Free local compute. Cloudflare challenges excluded and disclosed.</p>
<div class="stats"><div><strong class="number">{passed}/46</strong>strict passes excluding 4 Cloudflare skips<br><small>{passed}/50 including skips</small></div><div><strong class="number">808/808</strong>recovered after commit-window kill<br><small>Before fix: 708/808</small></div><div><strong class="number">{sustained['ok']:,}</strong>documents in sustained local trial<br><small>{sustained['wall_s']:.1f}s · {sustained['useful_documents_per_s']:.1f}/s</small></div></div>
<p class="note">Initial cohort: 34/40. Reserved cohort: 5/10. Four actual 404s, an invalid keyword, a robots redirect and a server redirect loop remain visible. Keyword checks are not extraction accuracy. GIMP succeeds in a separate configured recheck.</p>
<h2>Short demo · actual local execution</h2><video controls preload="metadata" poster="demo.png" src="demo.webm"></video>
<p>Two local origins; 162 useful documents per trial. JavaScript fallback, temporary errors, in-flight kill and commit-window kill. Exact expected URLs and content markers checked.</p>
<h2>What changed</h2><p>Atomic page + child-frontier commits prevent lost work. Section/card headers retain real titles. CMS prose extraction avoids layout tables. Redirect cycles stop immediately. Full local suite: 146 tests.</p>
<p><a href="summary.json">All timing and load receipts</a> · <a href="sites.csv">Per-site CSV</a> · <a href="content-audit.json">Content audit</a> · <a href="fixes.patch">Production diff</a> · <a href="../../docs/evaluation.md">Methodology and limitations</a></p>
<h2>All 50 first exposures</h2><div style="overflow-x:auto"><table><thead><tr><th>Site</th><th>Cohort</th><th>Original result</th><th>End-to-end</th><th>Characters</th></tr></thead><tbody>{rows_html}</tbody></table></div>
<h2>Limits of the evidence</h2><p>Single host, purposeful URL sample, two source revisions across first exposures. Synthetic throughput includes a local fixture and a configured delay. No web-scale capacity, provider parity, long-duration stability or universal extraction claim. Raw captures stay local.</p></html>''')
    inventory = {str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in output.rglob('*') if p.is_file() and p.name != 'sha256.json'}
    (output / 'sha256.json').write_text(json.dumps(inventory, indent=2))
    print(json.dumps({'passed': passed, 'skipped': skipped, 'sustained_ok': sustained['ok'], 'output': str(output)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('reports/portfolio-build'))
    parser.add_argument('--output', type=Path, default=Path('examples/evidence'))
    args = parser.parse_args()
    build(args.source, args.output)
