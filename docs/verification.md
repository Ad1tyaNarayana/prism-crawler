> Historical research note. For current results and the supported adaptive path, see [evaluation](evaluation.md) and [architecture](architecture.md). Raw reports referenced below stay local and are intentionally excluded from the source repository.

# Initial-build verification record

Verified locally on 23 October 2026 using an Apple M4 Pro, 24 GiB memory, macOS 26.7.1, and Python 3.13.4. This record describes the initial build, before the subsequent [50-site study](real50.md). Its [original fixture report](../reports/index-initial.html) and raw runs are preserved. No independent review or production deployment was performed.

## Selected implementation

Use the adaptive crawler without a model by default. It first uses HTTP and source-backed structured content, then an advertised JSON alternative, then Chromium when needed. Learned path-template observations can skip repeated unsuccessful HTTP attempts. The independent learned-routing test also verifies periodic HTTP rechecks.

| Strategy | Content checks, each run | Median elapsed | Observed range | Requests | Browser pages |
| --- | --- | --- | --- | --- | --- |
| HTTP | 15/18 | 0.310 s | 0.309–0.312 s | 26 | 0 |
| Browser first | 18/18 | 11.089 s | 11.079–11.099 s | 27 | 16 |
| Adaptive | 18/18 | 2.046 s | 2.025–2.052 s | 29 | 2 |

These are five interleaved runs of each strategy on the same 22-case local fixture corpus: 18 content cases and four refusal cases. Each strategy passed all four refusal checks on every run. Every request count matched the fixture server's independent receipts; forbidden fixture paths received no requests. Adaptive was 5.42 times faster than browser first on this corpus, with 87.5% fewer rendered pages, but made two more HTTP requests. It did not minimize every metric.

The initial measured runtime and fixture source SHA-256 is `855df9d17fd050e51aed42aba2aab5368bc86be2220a032d88965f42e7ac409d`. Dependency versions and the fixture contract hash are in [the saved initial summary](../reports/benchmark-initial/summary.json). The current result verifier checks the separately rerun fixture results against the current files.

## Behavioral and live checks

- 33 automated behavioral tests passed. The recorded output is in [the test log](../.audit/tests.log).
- Ruff checks passed for source, tests, and benchmark tools; `pip check` found no broken requirements. Shell scripts passed `bash -n`.
- Six public URLs across five origins were checked once per strategy. HTTP passed five content-marker checks; browser first and adaptive passed six. Adaptive used one browser page and 13 requests. The [raw live output](../reports/live/adaptive.json) includes extracted text and per-URL checks.
- The actual `scripts/demo.sh` command completed successfully and exported a 1,443-character JavaScript-rendered quotes page containing Albert Einstein. [The command log](../.audit/demo.log) and [JSONL output](../data/demo.jsonl) are retained.
- Chromium exercised the generated report's search, miss filter, expandable evidence and mobile layout. All 22 rows were available, no page JavaScript errors occurred, and the 390-pixel viewport had no page-level horizontal overflow. Tables scroll within their containers.
- A separate profiled run took 13.838 seconds across the three strategies; 12.871 seconds were in the event-loop polling call. The limiting factors are waits, browser setup and fixture latency, not demonstrated CPU saturation. [The profile](../reports/profile-summary.txt) is excluded from the five-run comparison.

## Local models

| Model | Broad decisions | Narrow content classification | Broad warm median |
| --- | --- | --- | --- |
| Laya multilingual | 13/24 | 11/24 | 14.9 ms |
| Laya English | 18/24 | 20/24 | 29.1 ms |
| KEV-0.8B | 15/24 | 24/24 | 47.0 ms |

Each task has 24 distinct development examples repeated five times. The broad task combines routing and pairwise parser selection. These numbers are not held-out production accuracy or calibrated confidence. Laya multilingual made confidently wrong parser decisions. KEV did well on short content classification but abstained on the longer ambiguity case at the configured 0.9 gate.

The final ambiguity case was solved by a deterministic parser improvement. Enabling local model advice added no measured extraction coverage in the end-to-end ablation. KEV and Laya remain optional; Jev has a compatible adapter but was not called. No paid hosted inference was used.

## Reproduce the checks

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check src tests benchmarks
.venv/bin/python -m benchmarks.run --repetitions 5
.venv/bin/python -m benchmarks.live
bash scripts/demo.sh
.venv/bin/python -m benchmarks.report
.venv/bin/python -m benchmarks.verify_results
.venv/bin/python -m benchmarks.verify_report
```

The report generator also reads the retained model experiment artifacts. Reproducing those requires the optional model setup and local KEV server described in the README. The CI workflow is supplied but has not been run on GitHub. See [the limits](limits.md) for unsupported content types, application flows, distributed operation, and unproven generalization.
