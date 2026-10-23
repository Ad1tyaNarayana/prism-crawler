> Historical research note. For current results and the supported adaptive path, see [evaluation](evaluation.md) and [architecture](architecture.md). Raw reports referenced below stay local and are intentionally excluded from the source repository.

# What the experiments establish

The selected default is the adaptive evidence router without a neural decision provider. Read the generated [results page](../reports/index.html) for current numbers. The source of each number is a raw JSON artifact, not a handwritten chart.

## Three crawler approaches

| Approach | Mechanism | Useful when | Expected failure |
| --- | --- | --- | --- |
| Lean HTTP | One HTTP representation with the common parser, including embedded JSON | Static documents, feeds, structured responses | Content exists only after JavaScript or at an advertised alternate API |
| Render every HTML page | Guarded Chromium for HTML, native parsing for JSON and XML, HTTP fallback on browser failure | Rendering is consistently necessary | Browser startup, asset requests, wait time, dependency failures |
| Evidence router | Structured representations and explicit alternate APIs first, learned rendering choice, browser fallback | Sites mix static and dynamic representations | Bad quality estimates, unfamiliar application state, restricted dependencies |

The third approach's more unusual part is its preference for an alternative representation of the content before emulating a browser. It never guesses private endpoints. It follows only advertised, same-origin JSON alternatives that pass normal access checks.

## Existing approaches

[Scrapy AutoThrottle](https://docs.scrapy.org/en/latest/topics/autothrottle.html) already adapts delays to latency, and [SitemapSpider](https://docs.scrapy.org/en/latest/topics/spiders.html#sitemapspider) already handles sitemap discovery. Those are established techniques. Scrapy is a strong choice for a production HTTP frontier with site-specific extraction code.

[Crawlee's AdaptivePlaywrightCrawler](https://crawlee.dev/python/docs/guides/adaptive-playwright-crawler) already predicts whether HTTP or browser rendering is needed and learns from prior pages. The idea of learned HTTP-versus-browser routing is not new. This project uses a smaller explicit rule based on saved outcomes, with a periodic cheaper-path recheck.

[Crawl4AI adaptive crawling](https://docs.crawl4ai.com/core/adaptive-crawling/) chooses links and stopping points using information gain. That optimizes which pages to visit for a task. This project's measured comparison focuses on which representation to fetch and how to extract it. Combining topic-aware traversal with this policy is a plausible next experiment, not a result established here.

Firecrawl is an existing managed option for scrape, crawl, structured extraction, and rendering. It was used to find current primary-source documentation during this build. The crawler itself makes direct HTTP and browser requests. Firecrawl and Exa were not benchmarked head-to-head, and no claim of superiority to either service is made.

## Model findings

We tested [Laya](https://huggingface.co/convaiinnovations/laya) locally through its Python runtime and [KEV-0.8B](https://github.com/jaredpalmer/kev) locally through its MLX System One server. Both projects publish open weights. The [Jev System One API](https://docs.typesafe.ai/api/system-one) shares the typed request shape, but Jev was not called because no paid inference was needed.

The broad evaluation contains twelve routing questions and twelve pairwise parser questions. Its labels and wording were saved before the first model run. The repeated trials use the same cases. They test stability and latency, not statistical generalization.

The broad zero-shot approach failed to justify automatic control. Laya multilingual chose candidate B in every pairwise parser case, including confidently wrong choices. KEV-0.8B performed better on routing but still struggled to compare two long candidate texts. Laya English performed better than its multilingual checkpoint on this development set. That result is specific to these questions.

The narrower experiment asks whether one snippet is substantive content or boilerplate. KEV-0.8B classified all 24 development snippets correctly. Laya English classified 20 correctly and Laya multilingual classified 11. These are development-set outcomes, not a calibrated production error rate.

The end-to-end ambiguity case is a longer page with a promotional main element and a real article. At the 0.9 threshold, KEV abstained. It assigned only about 0.61 probability to the promotional block being boilerplate and about 0.90 to the real article being content. Lowering the threshold just to make this example pass would not establish reliability. The failed run is preserved in `reports/pre-quality-ablation/`.

The deterministic fix scores every semantic container and penalizes highly repetitive sentences. It recovers the real article without model calls. The final model ablation therefore tests whether optional advice causes regressions or adds useful coverage. It does not establish an extraction-quality gain from neural assistance.

## Failures that changed the implementation

1. The initial baseline lacked structured extraction. It missed JSON-LD and hydration that the adaptive method could read. All strategies now share those parser capabilities, so the final comparison is less favorable to the adaptive method and more meaningful.
2. Chromium redirect handling produced a navigation race. A regression test now verifies same-origin redirects and relative script paths. HTTP redirects are resolved before the browser navigates. No raw server redirect is forwarded into Chromium.
3. The first live run failed certificate verification because this Python installation had no usable default CA bundle. The transport now uses an explicit public CA bundle with certificate verification enabled.
4. A real JavaScript page's title and login link passed the original quality gate. A link-density check now rejects that navigation-only shell and triggers rendering.
5. An article extractor reduced a country directory to its introduction. Repeated-record detection now preserves the full record list. The live check requires markers from both the first and last country.
6. A long promotional main element outranked a real article. Comparing all semantic containers and penalizing repeated sentences corrected the parser without relaxing model confidence thresholds.
7. A slow browser asset outlived its page and attempted a telemetry write after SQLite closed. Browser contexts now own, cancel and join their request handlers before shutdown. A delayed-asset regression verifies that every started request is recorded before storage closes. Stylesheets are optional because downloading them delayed a text-only example without improving its content.

## Measurement discipline

The fixture suite contains 18 content cases and four refusal cases. Refusals are scored separately. The corpus includes real JavaScript execution, delayed rendering, JSON-LD, hydration, an advertised JSON endpoint, RSS, a short notice, a table, malformed HTML, exact and near duplicates, HTTP redirects, robots precedence, an access wall, a transient rate limit, multilingual text, and a byte-limit refusal.

Five fresh runs per strategy rotate their execution order. Storage and browser contexts are fresh. The origin server independently records every received request. Browser startup belongs in end-to-end duration. Raw requests, response bytes, methods, parser outputs, and origin receipts are retained. Profiled runs are excluded from reported medians.

The timings are constrained by deliberate waits, rendering setup, and the fixture's delayed JavaScript, not sustained CPU throughput. The 5 ms local minimum delay is intentionally much smaller than the default 500 ms used on public sites. Network request counts and browser-page counts are more transferable than the local speed ratio.

The `bytes` metric counts decoded response-body bytes retained by the transport. It excludes HTTP headers, TLS overhead, rejected oversized chunks, and bytes that may have reached the network buffer before cancellation. It is not a wire-bandwidth measurement.

The live smoke test visits six URLs across five origins once per strategy. It checks known text markers and records raw text, but it is not a human-labelled completeness benchmark. Live timings are not used to claim a speed advantage.

All local inference has zero API token fees. Hardware purchase, electricity, network transfer, and operator time are not priced. Research connector usage is separate from the crawler runtime. No hosted model inference was purchased.

## Next experiments

A larger held-out corpus is needed before any broad accuracy claim. Label incomplete outputs, short notices, index pages, product variants, long tables, and international pages separately. Fit confidence thresholds on a calibration split and measure accepted-case errors on untouched examples.

The next useful optimization is likely cache-aware browser assets or domain-specific API adapters. A distributed frontier is a separate engineering project. A generative browser agent is an alternative for authorized interactive workflows, but it introduces action planning, token cost, and a larger access surface. Neither is implemented or benchmarked here.
