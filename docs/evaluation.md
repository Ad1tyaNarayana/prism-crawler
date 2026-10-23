# Prism: measured results and engineering assessment

Measured locally on an Apple M4 Pro, Python 3.13. Dates and source hashes are in [the evidence](../examples/evidence/summary.json). No vendor API calls or paid inference were used in this round.

## New-site evaluation

The manifest froze 50 distinct hostnames not in the earlier development manifest, before any target fetch. This was a purposeful varied sample, not a random sample of the web. Forty sites were tested first; ten stayed unrequested until the initial fixes and 146-test suite passed. Each site used a fresh database, robots enforcement, a 55-second run budget and frozen lexical expectations. Confirmed Cloudflare challenges were skipped without retries or replacement URLs.

| Cohort | Strict passes | Cloudflare skipped | Passes excluding Cloudflare |
|---|---:|---:|---:|
| Initial first exposure | 34/40 | 4 | 34/36 (94.4%) |
| Reserved first exposure | 5/10 | 0 | 5/10 (50.0%) |
| Combined first exposures | 39/50 | 4 | 39/46 (84.8%) |

Combined observations span two source revisions; they are not a single final-version run. The reserved ten are the cleanest estimate after the initial fixes, and their 5/10 result matters. Once inspected, both cohorts are development data. We did not rewrite the criteria or replace failed URLs.

Passing URLs took median 1.88s and p95 7.37s end to end, including crawler setup and teardown. First-cohort timing was not an isolated performance test; tooling work ran alongside part of it. Four Cloudflare exclusions were Europeana, MoMA, OECD and Stack Overflow. The original per-case `started_at` field was mistakenly populated at completion; phase start/finish and measured monotonic durations are authoritative. The reserved harness corrects that field.

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
| Short HTTP trial, 8 origins / 8 workers, 15ms fixture delay | 808/808 | 2.77s | 291.7/s | 68.8 MiB |
| Sustained final-source HTTP trial, 8 origins / 8 workers, 60ms delay | 8008/8008 | 96.73s | 82.8/s | 119.5 MiB |

The short HTTP matrix after the atomic fix measured 37.0, 153.6 and 291.7 useful documents/s at 1, 4 and 8 workers. These are single trials, not confidence intervals or a proven speed improvement. Final-source mixed trials additionally exercise actual Chromium rendering, first-request 503s and both crash windows; all expected documents must be recovered to generate this report.

The sustained trial's successful-page p95 is 98.2ms; worker CPU time is 42.34s. RSS samples every 250ms include crawler child processes and exclude the fixture server. CPU for recovery trials covers only the resumed process. Parent wall time includes process startup, monitoring and restart. Per-page latency excludes waiting in the frontier. The same machine hosts the source servers, whose configured delay bounds throughput. Documents have distinct markers but deliberately similar prose, exercising near-duplicate indexing. No deduplicated record is dropped from correctness accounting.

This is an approximately minute-scale, 8k-document local test, not an endurance study, internet throughput claim, distributed benchmark or evidence for millions of pages/day. Disk exhaustion, machine power loss and multi-host failover remain untested.

## Reproduce and review

See [README commands](../README.md), [source diff](../examples/evidence/fixes.patch), [per-site CSV](../examples/evidence/sites.csv) and [machine-readable receipts](../examples/evidence/summary.json). The short demo records an actual local mixed crawl and two real crash/restart trials; it makes no external network requests. The video and summarized evidence ship in `examples/evidence/`. Raw public-page bodies and local account files do not.

**Assessment:** worth showing as a transparent backend engineering project: adaptive retrieval, bounded networking, crash-safe frontier transactions, reproducible failure diagnosis and source-aware evaluation. It does not establish Exa/Firecrawl parity or universal coverage. The strongest story is the data-loss reproduction and fix, followed by the content-audit corrections—not a single accuracy percentage.
