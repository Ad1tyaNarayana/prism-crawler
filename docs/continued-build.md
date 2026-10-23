> Historical research note. For current results and the supported adaptive path, see [evaluation](evaluation.md) and [architecture](architecture.md). Raw reports referenced below stay local and are intentionally excluded from the source repository.

# Continued first-party crawler build

This pass adds local text-PDF extraction and bounded scrolling to our existing crawler engine. No hosted crawler, paid API or required model is used. HTTP/HTML libraries, pypdf and Chromium are building blocks.

The same 50-site development set was rerun once under each of the three strategies, plus five public Exa pages and two public research PDFs per strategy. These are fresh live results, with frozen source and unchanged checks. They are not an unseen evaluation or proof of universal coverage. This single repetition is not a performance study.

## Fresh live results

| Approach | Reviewed checks, same 50 | Exa pages | Public PDFs | Requests for 50 | Chromium pages for 50 |
| --- | --- | --- | --- | --- | --- |
| http | 30/50 | 5/5 | 2/2 | 99 | 0 |
| browser | 33/50 | 5/5 | 2/2 | 846 | 34 |
| adaptive | 33/50 | 5/5 | 2/2 | 374 | 4 |

The original Windy body-marker check remains unchanged. Its separate functional check requires actual weather values, a successful forecast response and HEAD probe, and no offline banner. IKEA stays in the denominator when its requested item cannot be recovered.

## Previously failing real examples

| Example, adaptive | Fresh outcome | Characters | Supplemental functional check |
| --- | --- | --- | --- |
| archive | ok | 5245 | pass |
| openstax | ok | 2091 | pass |
| windy | ok | 505 | pass |
| ikea | incomplete | 0 | n/a |
| nature | out_of_scope | 0 | n/a |
| nhk-ja | error | 0 | n/a |

Exa About and Pricing previously failed because the parser selected short fragments. Those earlier extraction repairs remain in place. The new verifier detects those fragment failures, and these fresh runs check that the full content still survives. The installed CLI also extracted About and Pricing directly; [its exported documents](../reports/continuation/cli-live.jsonl) retain 10,889 and 4,828 characters respectively. Run `bash scripts/demo-recovery.sh` to repeat that example.

## Failures reproduced and fixed in this pass

| Controlled CLI example | Before, all strategies | After HTTP | After browser | After adaptive |
| --- | --- | --- | --- | --- |
| /deferred | ok | incomplete | ok | ok |
| /paper.pdf | not_enqueued | ok | ok | ok |
| /infinite | ok | incomplete | incomplete | incomplete |
| /long.pdf | not_enqueued | incomplete | incomplete | incomplete |
| /scan.pdf | not_enqueued | unsupported | unsupported | unsupported |
| /locked.pdf | not_enqueued | unsupported | unsupported | unsupported |
| /malformed.pdf | not_enqueued | unsupported | unsupported | unsupported |
| /private/paper.pdf | not_enqueued | robots_denied | robots_denied | robots_denied |

Before scrolling, the deferred page falsely passed as readable content while omitting the later archive paragraph. Browser-first and adaptive now include that paragraph. HTTP-only reports unfinished content rather than claiming complete extraction. PDF seeds were previously skipped without any document result. They now have explicit outcomes.

Infinite content and PDFs exceeding extraction limits are marked incomplete and do not export partial text as successful content. Unsupported, incomplete and policy-refusal outcomes are not counted as extraction successes.

## What did not work

The first PDF implementation failed on this macOS host because setting RLIMIT_DATA was rejected. That failed run is preserved in after-local. The revised child process uses a CPU limit, a parent wall deadline, bounded decoding, and a periodic RSS watchdog. Linux additionally uses OS data and address-space limits. The watchdog is best effort and is not a hard macOS memory ceiling.

The first scroll implementation also waited for unrelated background scripts on pages whose initial content already passed verification. The Exa browser development run exposed this cost and was interrupted; its completed HTTP results and partial SQLite state remain in supplemental. Verified initial content now waits for visible content to settle during scrolling, while unfinished pages retain the full dependency wait. A regression with a blocked background script checks that verified content completes before its browser deadline.

The first corrected PDF run labelled a truncated repetitive document low_quality because readability took precedence. The final acceptance order preserves source-limit failures as incomplete before considering readability. The failed run is preserved in after-local-fixed.

## Remaining live failures

| Site, adaptive | Outcome | Reason |
| --- | --- | --- |
| britannica | access_wall | HTTP 403 access restriction |
| noaa | robots_denied | Robots unavailable or restricted, HTTP 403 |
| usgs | access_wall | Visible access or authentication wall |
| mayoclinic | robots_denied | Robots unavailable or restricted, HTTP 403 |
| nature | out_of_scope | Origin is outside the explicit crawl scope |
| metmuseum | robots_unavailable | Robots unavailable or restricted, HTTP 429 |
| smithsonian | access_wall | HTTP 403 access restriction |
| britishmuseum | access_wall | HTTP 403 access restriction |
| ikea | incomplete | Redirect lost the requested resource identifier; Redirected to a different target |
| patagonia | access_wall | Visible access or authentication wall |
| bookshop | access_wall | HTTP 403 access restriction |
| reuters | robots_denied | robots.txt disallows this URL |
| apnews | access_wall | HTTP 403 access restriction |
| nhk-ja | error | TimeoutError:  |
| khanacademy | access_wall | Visible access or authentication wall |
| windy | ok | Original content markers missing: forecast |
| lobsters | robots_denied | robots.txt disallows this URL |

No robots denial, unavailable robots file, access wall, login flow or external origin is bypassed. Nature requires an out-of-scope identity/cookie flow. Wrong-item redirects cannot recover missing original products. Timeouts remain errors when permitted requests cannot finish. Fresh outcomes can differ from historical outcomes as websites change.

## Verification and limits

All 81 tests pass. New tests exercise PDF discovery, file-type sniffing, protected and malformed files, page truncation, deferred content, bounded infinite growth, and robots enforcement on scroll-triggered requests. The actual installed CLI was driven under all three strategies. Local server receipts show zero robots-disallowed content requests.

Scrolling defaults to six steps, configurable with --scroll-steps between 0 and 20. It respects the existing page deadline. It does not click buttons or cover every nested scroll container, delayed timer or virtualized list. Adaptive detection uses explicit unfinished-content signals and can miss sites without them.

PDF extraction is bounded to 16 MB input, 100 pages, 200,000 characters, a 10-second worker deadline and six CPU seconds. It extracts existing text and records empty pages as uncertainty. Encrypted, image-only or blank PDFs are unsupported; OCR is not implemented. The pypdf documentation explains [text-layer and extraction limits](https://pypdf.readthedocs.io/en/6.21.1/user/extract-text.html).

Fresh live scores are not attributed solely to this change. This is a repeated development set, the network is variable, and there is only one live repetition. No speedup or model-quality claim is made.

## Reproduce and inspect

From /Users/aditya/Me/optimal-crawler:

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m benchmarks.continuation_local --output reports/continuation/new-local
.venv/bin/python -m benchmarks.real50 --phase after --repetitions 1 \
  --output-root reports/continuation/new-live50 --profile benchmarks/technical-profile.json
.venv/bin/prism crawl https://arxiv.org/pdf/1706.03762 --max-pages 1 \
  --no-discover --no-follow-links --db data/paper.db
```

Use fresh output paths. Existing evidence is preserved. The live runs retain request telemetry, extracted text, compressed response bodies and a source snapshot. Local request telemetry is not an independent live server receipt.

Runtime SHA-256: `1d561b1697ccd7c91ce10132caf7477dd9d22f12beeb2a0326de09727e887078`.

Evidence: [comparison](../reports/continuation/comparison.json), [all 150 site outcomes](../reports/continuation/results.csv), [local before](../reports/continuation/before/results.json), [local final](../reports/continuation/local-release/results.json), [live phase](../reports/continuation/live50-release/after/phase.json), [supplemental phase](../reports/continuation/supplemental-release/phase.json).
