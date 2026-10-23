> Historical research note. For current results and the supported adaptive path, see [evaluation](evaluation.md) and [architecture](architecture.md). Raw reports referenced below stay local and are intentionally excluded from the source repository.

# Content verification and bounded recovery

Prism owns the crawler engine: the frontier, politeness and access policy, routing, extraction selection, deduplication, scheduling, verification and recovery. It uses Python HTTP/HTML libraries and Chromium as building blocks. This change introduces no crawler framework, hosted crawler, paid API or required model.

The previous acceptance rule could accept a readable metadata fragment because its quality score exceeded 0.5. That prevented adaptive rendering even when the main page had not loaded. A successful HTTP response and a readable paragraph are insufficient evidence of successful extraction.

## What changed

`ContentVerifier` independently reads the fetched source and attaches an assessment to each extraction candidate. It checks observed main headings and substantive text blocks, executable loading shells, and numeric resource identifiers lost during redirects. Candidate selection first requires a usable quality score and an accepted assessment. Optional local model advice receives only eligible candidates.

A recoverable incomplete result can try up to three advertised same-origin alternatives, then one browser attempt. API alternatives retain the original page's content evidence. A learned browser route cannot launch another browser attempt for that same page after failing. The browser's initial content check uses the verifier too, preventing a metadata description from shortening the wait for a loading shell.

An unresolved gap is exported as `incomplete`, with an explanation and verification metadata. Its text is not exported as successful content. It counts as an unsuccessful route observation and does not replace the accepted page cache. Cached HTTP content is conditionally revalidated only when it carries the current verifier version; older content is fetched and checked again. Robots, access walls, origin policy and request budgets retain their existing enforcement.

## Evidence

The new regression cases exercise delayed JavaScript content behind a substantial description, a higher-scored fragment competing with complete content, item identity lost on redirect under all three strategies, a failed learned browser route, legacy cached content, and incomplete versus complete API alternatives. The existing suite covers transport policy, browser dependency handling, recrawling, extraction and the CLI. The final complete run passed **72 tests**, and Ruff passed on the runtime, tests and new demonstration/replay runners.

The retained-response replay uses repetition zero from each strategy in the final 50-site study, final five-page Exa study, and earlier Exa study. It evaluates only previously successful documents with a matching captured body: **131 page/strategy records**, with no missing captures and no network requests. Repeated URLs across strategies and studies are not independent sites.

| Replay observation | Result |
| --- | --- |
| Current extraction accepted by the new checks | 128/131 records |
| Current extraction rejected for lost item identifier | 3/131 records, all IKEA |
| Earlier Exa fragments rejected by section/block checks | 7 records |

The IKEA records passed the old keyword checks even though their requested item identifiers disappeared after redirect. These are now reported as identity uncertainty. The current extractor's fuller versions of all seven earlier Exa fragments pass. Those extraction fixes preceded this verification change; the new contribution is detecting the fragments as incomplete. The earlier website reports and their source snapshots remain unchanged.

This replay does **not** measure live recovery, current website coverage, cost savings or speed. It cannot establish that every accepted document is complete. The raw assessments are in [replay.json](../reports/verification/replay.json).

The actual installed CLI was also driven against five controlled local pages. It extracted the static page through HTTP, recovered the delayed JavaScript page through the browser, marked a wrong-item redirect incomplete, refused a robots-disallowed page, and reported a 403 access wall. The server received **zero requests** for the disallowed content. [Exported documents](../reports/verification/cli-final/pages.jsonl) and [server receipts/assertions](../reports/verification/cli-final/evidence.json) preserve the result. This is a local demonstration, not a live-site benchmark.

## Reproduce

From the project directory:

```sh
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests benchmarks/verification_replay.py benchmarks/verification_demo.py
.venv/bin/python -m benchmarks.verification_demo --output reports/verification/new-demo
.venv/bin/prism crawl https://example.com --max-pages 10 --db data/example.db
.venv/bin/prism export --db data/example.db --output data/example.jsonl
```

Use a fresh demo output directory so prior evidence is preserved. The replay's input files and runtime fingerprint are recorded in its JSON. Its runner is `benchmarks/verification_replay.py`; pass those inputs followed by `--output` and a new result path.

## Limits

Acceptance means no gap was detected by the implemented checks. It is not a calibrated confidence probability or a promise of universal completeness. Pages with insufficient source structure, structured JSON without independent page evidence, hidden pagination, canvas content, closed shadow roots, PDFs and image-only content still require additional capabilities. This change does not add scrolling, arbitrary browser actions, OCR or authenticated sessions.

Evidence is bounded to 64 headings and a uniform sample of up to 128 substantive source elements. Paragraph matching uses token anchors and can miss paraphrases or fail on unusual layouts. For content over 200,000 characters, coverage checks are skipped and the evidence limit is recorded. The numeric identifier check is conservative: a legitimate canonical redirect can change an identifier, so its result is uncertainty rather than proof that the destination is wrong. Generic thresholds can produce both false positives and false negatives.

Future work should address these limitations through separately testable capabilities in our own engine. Access refusal remains a supported terminal result.
