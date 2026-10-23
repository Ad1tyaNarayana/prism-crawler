> Historical research note. For current results and the supported adaptive path, see [evaluation](evaluation.md) and [architecture](architecture.md). Raw reports referenced below stay local and are intentionally excluded from the source repository.

# Fifty-site real-web comparison

All three strategies were tested on one URL from each of 50 websites not used to tune the initial build. Five interleaved runs per strategy were captured before general fixes, followed by five after them. All 1,500 page attempts remain in the evidence. No model inference or paid crawling service was used.

| Strategy | Before, reviewed checks | After, reviewed checks | Median elapsed before → after | Requests, median | Browser pages, median |
| --- | --- | --- | --- | --- | --- |
| HTTP only | 24/50 | 29/50 | 45.93 → 46.94 s | 98 → 99 | 0 → 0 |
| Browser first | 24/50 | 30/50 | 119.94 → 117.93 s | 340 → 417 | 36 → 34 |
| Adaptive | 24/50 | 30/50 | 48.93 → 49.93 s | 132 → 190 | 5 → 4 |

These checks require topic coverage and minimum content, plus four additional requirements found during inline inspection of the first baseline outputs. The original topic checks are preserved unchanged. Their counts are in `comparison.json`. The additional checks reject serialized React state, a missing mathematical equation, a truncated course description, and a product redirect to a category page. All checks apply identically to before and after outputs.

Improved sites: mathworld, who, nhs, react, go-tour, coursera. Sites with fewer passing repetitions after the fixes: none.

## What changed

- **WHO.** Valid security headers exceeded the HTTP client's 8 KiB field limit. The limit is now bounded at 64 KiB; certificate checks and robots policy remain active.
- **NHS and Coursera.** An isolated FAQ answer or metadata description outranked the complete page. Longer credible document candidates now outrank those fragments.
- **React.** Serialized component state was saved as prose. Encoded JSON containers are unpacked or skipped so readable source content can win.
- **MathWorld.** Image-based equations disappeared. Equation and MathML alternative text now survives extraction.
- **Go tour.** The renderer stopped while application dependencies were still loading. Empty apps now use the existing total page budget; static content can finish early.
- **Failure reporting and shutdown.** Challenge, waiting-room and offline error pages are no longer counted as content. Route completion survives cancellation, and accessible open shadow roots can be serialized.

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
| britannica | access_wall | 0/5 | HTTP 403 access restriction |
| noaa | robots_denied | 0/5 | Robots unavailable or restricted, HTTP 403 |
| usgs | access_wall | 0/5 | Visible access or authentication wall |
| mayoclinic | robots_denied | 0/5 | Robots unavailable or restricted, HTTP 403 |
| nature | out_of_scope | 0/5 | Origin is outside the explicit crawl scope |
| archive | low_quality | 0/5 | No extraction candidate reached quality threshold |
| metmuseum | robots_unavailable | 0/5 | Robots unavailable or restricted, HTTP 429 |
| smithsonian | access_wall | 0/5 | HTTP 403 access restriction |
| britishmuseum | access_wall | 0/5 | HTTP 403 access restriction |
| ikea | ok | 0/5 | Redirected to a different target |
| patagonia | access_wall | 0/5 | Visible access or authentication wall |
| bookshop | access_wall | 0/5 | HTTP 403 access restriction |
| reuters | robots_denied | 0/5 | robots.txt disallows this URL |
| apnews | access_wall | 0/5 | HTTP 403 access restriction |
| nhk-ja | error | 0/5 | TimeoutError:  |
| openstax | low_quality | 0/5 | No main content found |
| khanacademy | access_wall | 0/5 | Visible access or authentication wall |
| edx | unsupported | 0/5 | Decoded response exceeds byte limit |
| windy | low_quality | 0/5 | Application failed to load its content |
| lobsters | robots_denied | 0/5 | robots.txt disallows this URL |

Robots refusals and access restrictions remain in the 50-site denominator, but are listed separately from extraction failures. The unavailable IKEA product is not replaced with an easier URL. Some successful outputs retain navigation, editorial notes or bibliography. Passing these checks is not proof of complete or perfectly clean extraction.

## Measurement limits

Each run uses a fresh database and browser. Four workers, four browser slots, a 500 ms minimum per-origin delay, a 15-second request timeout, a 3-second rendering allowance, a 4 MB response limit and 1,500-request budget are shared across strategies and phases. The browser's total deadline is unchanged. Sitemaps and link traversal are disabled to isolate recovery of the same 50 URLs. Models are disabled.

The before and after phases ran at different times. Strategies rotate order within each phase, but public sites, networks and caches can still change. The runtime ranges and all five values are retained. Treat duration as an observed cost, not a controlled causal speedup. Request totals come from local crawler telemetry, not independent receipts from the public servers. Bytes measure retained decoded bodies, not wire traffic.

A separate profiled adaptive run took 54.82 seconds, with 50.22 seconds in event-loop polling. It is excluded from the comparison. The measured path is dominated by waiting for network activity and deliberate pacing, rather than CPU saturation. The before/after duration ranges overlap for each strategy; these fixes establish a recovery improvement, not a measurable speed improvement.

This set was new before the baseline. It became a development set when its failures informed fixes. The rerun does not establish accuracy on a new untouched set, and the sample is hand-selected rather than random. English dominates; French, Spanish, German and Japanese targets are also included. No claim of universal website support is made.

Manifest SHA-256: `873e07ed704edf4c58b3136793225e3d95e9ab92b86e2400786ec84ad4bb798f`.
Before runtime SHA-256: `b1ca35afda115a462b4f36036cc6209b7ea2f03e53f647c68c5ad4b50e966a9e`.
After runtime SHA-256: `134ef3e3ca29a88dbb9f546e078862edb75766ff52df51050aceaffe395a9609`.

Completed phases reject mixed source versions and preserve completed run files. The exact measurement script is saved beside each phase. To repeat the current implementation, run `.venv/bin/python -m benchmarks.real50 --phase after --repetitions 5 --output-root reports/real50-recheck`. The new output directory keeps these original results intact. The current runner records the imported package path; the measured phases used the saved earlier harness with the same scoring and execution settings.
