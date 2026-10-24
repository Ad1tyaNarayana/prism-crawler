# Local comparison with Crawlee and Crawl4AI

This experiment compares specified configurations on frozen public URLs. It does not rank every possible use of these libraries. Raw response bodies and extracted content stay in ignored local reports; safe numerical summaries can be shared separately.

## Protocol

The manifest `benchmarks/oss50.json` freezes 50 URLs with hostnames outside the previous two 50-site manifests. It is a purposeful sample, not a random sample of the web. Thirty sites are development cases; twenty are reserved until initial Prism fixes are complete. Invalid URLs, policy failures and challenges remain recorded. No frozen URL is replaced.

Reference qualification is separate from provider measurement. It fetches the source with robots enforcement and also tries native Chromium. A source-only review identifies relevant content regions and exclusions before the provider outputs are opened. Reference blocks use an HTML5 parser independent of Prism's extraction/verifier. Content cards inside navigation remain eligible. Manual source selectors belong only to the reference labels and are never supplied to a crawler's extractor.

This is a single model-assisted source review, not a blind independently human-labelled corpus. Sources with too little reference content remain retrieval observations without a primary quality score. References capture what was available under the declared scope and dependency settings; they cannot certify all content on a website.

## Configurations

- Prism uses the supported adaptive engine and its generic parser/verification candidates.
- Crawlee 1.11.0 uses `AdaptivePlaywrightCrawler.with_beautifulsoup_static_parser`, its default rendering predictor, and a fixed generic Trafilatura recall-oriented extraction handler with a semantic text fallback. The handler's simple adequacy check can trigger rendering. Crawlee is a framework, so these quality results describe that handler/configuration rather than an intrinsic article extractor.
- Crawl4AI 0.9.5 uses `AsyncWebCrawler`, local Chromium, `CacheMode.BYPASS`, its default Markdown generator with a fixed pruning filter, and raw Markdown fallback when fit Markdown is empty. Both raw and fit output are retained for inspection. No LLM extraction is enabled.

Each measured observation has a fresh Python subprocess, storage/database and browser context. All environments use Playwright 1.64.1 and the same installed Chromium build. The corrected profile fixes browser viewport at 1280x720, locale en-US and timezone UTC. Provider order rotates across sites and three repetitions. There is no learning across separate one-URL jobs. In particular, this cold-start workload does not assess Crawlee's learned rendering predictor on repeated pages of a website.

All providers use the same honest benchmark user agent, a 45-second library deadline, 12-second ordinary request timeout where supported, a 140-request limit, no frontier traversal and no deliberate retries. A 65-second parent limit bounds import/cleanup failures. Browser navigation uses a 30-second bound where supported. The framework adapters use a one-second settling allowance; Prism's own content-readiness rules may finish earlier or later within its page budget.

Stealth, fingerprint generation, session rotation, proxies for access escalation, simulated human interaction and authentication are disabled. HTTP/browser caches are not reused between observations. Images, media and fonts are blocked. Requests use valid TLS verification.

## Policy enforcement and overhead

Prism uses its production network guard. Competitor HTTPX/httpx2 and Playwright traffic use a benchmark-only gate that enforces robots, public destinations, origin scope, GET/HEAD methods and request budgets while preserving native network retrieval. Framework robots helpers are disabled to avoid duplicate policy lookups; this does not disable the shared guard. Local fixture receipts verify that a robots-denied endpoint receives zero requests through every adapter.

The corrected gate keeps document and asset pacing in separate lanes, with 0.5-second document and 0.05-second asset start spacing. A declared robots Crawl-delay applies across both lanes. Public-address checks are cached per hostname within one observation. This benchmark helper is not a production DNS-rebinding sandbox. Scope includes the seed and explicitly recorded source-advertised script/style origins and permitted observed www/apex/subdomain redirect helpers. Unlisted dependencies remain denied.

The selected user agent and dependency scope can affect site behavior. A challenge on a browser dependency excludes the entire case for every provider; this does not imply that the seed HTML itself was inaccessible. The final validation pre-excludes the union of previously confirmed challenge cases.

Confirmed Cloudflare challenge responses stop further attempts. A shared exclusion removes that case from paired quality/timing comparisons, including observations completed before another adapter found the challenge. Counts before and after exclusions must both be reported. Ordinary Cloudflare-served content is not itself a challenge. Other access restrictions remain explicit failures.

The policy gate and its overhead are part of competitor measurements. This is a policy-constrained configuration comparison, not unrestricted native framework throughput. Receipts preserve denied dependencies and request counts so a policy effect can be distinguished from extraction quality.

## Quality metrics

The scorer is locked before comparative observations and never uses Prism's internal quality score. It computes source-token precision, recall and F1 using token multisets, weighted source-block recovery using token trigrams, heading coverage and explicit source-boilerplate matches. CJK text is segmented into characters rather than treated as one whitespace-delimited word. Markdown syntax and link destinations are normalized consistently.

A strict quality pass requires source-token precision and recall of at least 0.80, weighted block recovery of at least 0.75, at least 0.70 heading recovery when three or more headings exist, and matched boilerplate token ratio at most 0.10. Continuous scores and original outputs matter more than the binary gate. The code-block metric measures token recovery; it does not establish executable syntax or semantic correctness.

Token overlap is a proxy for source-content recovery, not a factuality metric. Dynamic content, repeated text and imperfect region labels can affect it. Source/output spot checks should inspect representative wins and losses. Label changes after observing outputs require a separately named sensitivity analysis and must not rewrite the primary score.

### Reference construction audit

The frozen development references use `semantic-blocks-v1`. Inspection during the baseline found that this builder omits descriptions written directly in divs instead of paragraphs, and short table cells such as platform names. This can mark useful content as extra noise; Greenwich, Cleveland, CNRS and Quarto are examples. A separately saved `semantic-and-unwrapped-v2` full-text sensitivity analysis includes those text runs and short content labels without duplicating enclosing semantic blocks. An earlier sensitivity draft with a three-token cutoff is also retained locally; the final full-text version records a one-token cutoff. The original development labels and scores remain intact. These post-hoc corrections are not an independent validation set.

Reserved-site references use v2, locked before provider outputs. The token scorer and pass thresholds are unchanged. Neither reference version measures full visual fidelity. Animated terminal examples can include hidden duplicate code and controls in the source reference; report such disagreements as label/output limitations rather than automatically treating every low precision score as boilerplate.

## Timing and browser cost

End-to-end wall time includes Python imports, library startup, network work, extraction, shutdown and monitoring. Separate library elapsed and browser-launch times are retained. Browser cost is observed page creation and launch count, not an estimate based on the chosen strategy. Process-tree RSS is sampled every 250ms and excludes the orchestrator. Failed/empty responses are not credited as useful work.

Compare speed on shared quality-passing cases as well as the complete scored workload. Report sample counts and three-run variation; three observations per configuration do not establish tight confidence intervals. Do not extrapolate cold one-page trials into production pages/day or claim all configuration options were optimized.

## Excluded pilot

The first live pilot under `reports/oss-compare/baseline-dev/` was stopped after discovering different browser viewport defaults and unnecessary coupling of asset pacing to document delay. Its partial receipts and source snapshot remain intact. The primary corrected comparison is stored separately under `baseline-common`. No Prism production fix is included in the pilot-to-primary harness correction.

## Reproducing the experiment

Use isolated environments; competitor packages are not Prism runtime dependencies. The recorded phase directories contain complete installed-package inventories. The following commands install the directly pinned benchmark dependencies used here:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,benchmark]' 'playwright==1.64.1'
python3 -m venv .runtime/crawlee-env
.runtime/crawlee-env/bin/python -m pip install 'crawlee[adaptive-crawler,httpx]==1.11.0' 'trafilatura==2.3.1' 'protego==0.7.0' 'aiohttp==3.14.5' certifi httpx 'playwright==1.64.1'
python3 -m venv .runtime/crawl4ai-env
.runtime/crawl4ai-env/bin/python -m pip install 'crawl4ai==0.9.5' 'protego==0.7.0' 'playwright==1.64.1'
.venv/bin/python -m playwright install chromium
PYTHONPATH=.:src .venv/bin/python -m benchmarks.oss_calibrate --output reports/my-calibration
```

Collect references with `benchmarks.oss_reference`, review their actual sources and create region annotations, then lock labels with `benchmarks.oss_gold --include-leaf-text`. This source-review step is required; the automatic proposal is not a human ground truth. Run `benchmarks.oss_compare --gold PATH --output NEW_PATH --repetitions 3`, then summarize completed phases with `benchmarks.oss_report PHASE... --output REPORT_PATH`. Every measurement output path must be new. Do not run competing benchmark or test jobs during timed phases.

Fresh requests can encounter different content, policies, network conditions or challenges. A rerun is not expected to produce identical live results. No credentials or hosted inference service are needed.
