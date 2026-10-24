# Local retrieval comparison: Prism, Crawlee and Crawl4AI

This is a comparison of specific local single-page retrieval configurations. It measures content recovery, cold-start latency and observed browser use. It does not measure distributed crawl capacity, traversal quality or every possible configuration of these libraries.

The 50 URLs were frozen before requests and use hostnames outside the previous two 50-site manifests. Thirty were development cases; twenty remained unrequested until the initial fixes. The sample is deliberately varied, not statistically representative of the web. No paid API, proxy, hosted model or challenge solver was used.

See the [method and reproduction commands](oss-benchmark-method.md), [numerical results](../examples/oss-evidence/summary.json) and [source/configuration fingerprints](../examples/oss-evidence/phase-fingerprints.json). Source snapshots and full outputs remain local.

## Final validation after the fixes

All three configurations made fresh requests on the same frozen 50 URLs after the final serializer fix. Eleven known Cloudflare cases were pre-excluded, eight lacked accessible reference sources under the declared policy, and Stats NZ had insufficient reference content. Thirty sites received content scores. All fifty remain visible in the per-case evidence.

| Configuration | Source-token F1 | Content-block recovery | Strict quality passes | Median cold job | Browser trials |
|---|---:|---:|---:|---:|---:|
| Prism, verifier v7 | 97.3% | 98.6% | 28/30 | 1.42s | 1/30 |
| Crawlee + Trafilatura | 76.5% | 70.8% | 12/30 | 4.96s | 30/30 |
| Crawl4AI + pruning/Markdown | 83.7% | 85.9% | 22/30 | 3.62s | 30/30 |

This is one interleaved final validation repetition after tuning, not thirty new unseen sites or a repeated performance estimate. The earlier three-repetition holdout below is preserved separately. On the eleven shared strict-passing cases, median cold times were 0.95s, 4.50s and 3.17s. Across scored sites, median request counts were 2, 12 and 9.5; median sampled process-tree peak RSS was 62.6, 747.1 and 592.2 MiB respectively.

Prism returned nonempty OK content on 30/39 non-Cloudflare URLs, but only 28 met the strict quality criteria. The remaining two quality failures are FastAPI's animated terminal example and Fairphone's surrounding newsletter/navigation content. This distinction prevents counting every nonempty response as useful extraction. The nine other non-Cloudflare cases remain unavailable or unscorable; they are not hidden quality passes.

The final live JAXA check reaches 95.9% F1 and 100% content-block recovery without a browser. Svelte recovers both code blocks with preserved delimiters and indentation, reaching 99.1% F1. National Archives retains all five cards at 98.5% F1 without a browser. Quarto reaches 97.7% F1 using one browser page in 8.52s.

The source and adapters remained unchanged during the completed phase. All 170 local tests pass. The work does not establish a statistically reliable general speedup: the sites, cold-start profile, policy overhead and quality labels constrain the result.

## Untouched holdout, before the final fixes

The twenty-site first-exposure phase has three repetitions per provider. Four cases were excluded for confirmed Cloudflare challenges, including one first detected in browser dependencies. Three additional cases lacked accessible references under the declared policy. The remaining thirteen sites supply 39 scored trials per provider.

| Configuration | Source-token F1 | Weighted content-block recovery | Strict passes, all three repetitions | Median cold job | Browser trials |
|---|---:|---:|---:|---:|---:|
| Prism adaptive, verifier v5 | 94.2% | 98.7% | 12/13 sites | 1.44s | 0/39 |
| Crawlee 1.11.0 + fixed Trafilatura handler | 80.0% | 75.1% | 5/13 sites | 4.53s | 39/39 |
| Crawl4AI 0.9.5 + fixed pruning/Markdown | 87.5% | 90.9% | 10/13 sites | 3.72s | 39/39 |

Quality first takes the median across repetitions for each site, then averages sites equally. Latency is the median of site medians, includes startup and policy checks, and includes quality failures. On the five shared all-provider strict-passing sites, median site times were 0.88s, 4.22s and 3.10s respectively. Full per-trial timings and variation are retained locally.

This cold one-URL workload starts Crawlee without learned site history. Its observed browser use is not an estimate of warmed adaptive crawling. Its content scores describe the frozen extraction handler, not an intrinsic Crawlee article extractor. Competitors also use a common benchmark policy gate with measurable overhead; these are not unrestricted native throughput numbers.

## What broke, and what changed

**National Archives lost content cards.** The page puts its five descriptive child-page cards inside navigation. Prism removed the navigation before extracting or checking the source, so the verifier could not detect the loss. Content navigation containing substantial article cards is now retained inside main/article regions. Ordinary menus are still removed. Full-text source-token F1 rose from 60.8% to 98.5%; weighted block recovery rose from 41.6% to 100%. These are three-repetition focused live results under the separately labelled post-hoc full-text references. Median time changed from 2.99s to 3.27s; no browser was needed.

**Quarto's introduction hid an empty download area.** HTTP returned enough readable prose to pass, but JavaScript had not populated the download table. The verifier now recognizes simple inline fetch-and-target-write code associated with an empty content region. This triggers guarded browser recovery without evaluating the script during routing. Full-text F1 rose from 47.2% to 97.7%, and block recovery from 27.5% to 96.2%, across three focused live repetitions. Median time rose from 1.94s to 9.12s and browser use from zero to one page per job. Better completeness has a real cost.

**JAXA exposed legacy global chrome.** The holdout page's header and footer are divs rather than HTML landmark elements. Prism retained the content but also large global menus: F1 was 47.4%, despite 100% block recovery. Link-heavy header/footer containers outside content regions are now removed. Tests protect article metadata, descriptive content cards and prose in header-named containers. The saved-source check reaches 96.1% F1 with full block recovery; subsequent live validation is reported separately from the untouched holdout.

**Highlighted code lost its syntax.** A qualitative review of Svelte found newlines inserted between every highlighting span, including between a number and its CSS unit. Python indentation also disappeared. Token overlap mostly hid the problem. Text serialization now joins inline text as the DOM does and preserves preformatted whitespace, line breaks and token adjacency. DOM entities are decoded only by the HTML parser. Regression tests extract highlighted Python, compile and execute it, and check markup/CSS fidelity. The saved Svelte source now recovers both reference code blocks instead of one. This is a fidelity check beyond the primary token score, not a claim that all extracted code is executable.

## Failures retained in the accounting

The frozen URLs include a non-resolving Starlette www hostname, actual Inria and GNOME 404s, robots redirects outside the declared helper scope, and unavailable robots responses. Stats NZ produced too little usable reference content to score. None was replaced with a working URL. Some unavailable references later produced content intermittently; those remain unscored retrieval observations rather than retroactively manufactured quality successes.

The Prism development rerun also recorded new Max Planck and Fairphone connection/header timeouts under the 12-second request budget. They are retained as failures. A later successful request does not erase them. The first-exposure holdout and later tuned validation must not be merged into an untouched final accuracy claim.

Getty showed a different failure: Crawl4AI's rendered snapshot contained a client-side error concerning an undefined feature list, while Prism's HTTP response retained the About page content. Raw Markdown still contained the error and navigation. This supports keeping an intact server response when browser execution damages the page under the configured dependency scope; it does not prove how every configuration would behave.

A uniform raw-Markdown sensitivity check confirms the pruning tradeoff. On the thirteen holdout sites, selecting raw output for every Crawl4AI result raises block recovery from 90.9% to 92.6%, but lowers F1 from 87.5% to 64.7%. On the final thirty-site validation, block recovery rises from 85.9% to 89.3% while F1 falls from 83.7% to 64.4%. These reuse the same retrievals and timings; no per-site best-output selection or extra live requests are involved. The [holdout](../examples/oss-evidence/raw-holdout-audit.json) and [final](../examples/oss-evidence/raw-final-audit.json) numerical audits preserve both choices.

Fairphone illustrates a remaining tradeoff. The Trafilatura handler drops some substantive sections while returning less surrounding text. Prism keeps more content but also newsletter/navigation material. FastAPI's animated terminal examples further expose the limits of source labels and plain text output. These cases need content-level inspection; a single F1 number is insufficient.

## Evaluation mistakes we kept visible

The first pilot had different browser viewport defaults and coupled asset/document pacing. It was stopped, preserved and excluded. All adapters then passed nine local static/JavaScript/robots-denial calibration checks under the corrected profile.

The original frozen development reference builder omitted descriptions directly inside divs and short table labels. It penalized useful output on Greenwich, Cleveland, CNRS and Quarto. Original labels and scores remain intact. A separately labelled post-hoc full-text sensitivity analysis includes these text runs and short labels; the untouched holdout used that revised builder before provider outputs existed. Reference review is model-assisted, not an independent human-labelled ground truth.

An intermediate final pass was also stopped after the code-fidelity audit found the span/indentation bug. Its receipts remain local. The completed validation after that fix is a tuned regression check, not another blind benchmark.

## What the evidence supports

Prism is effective at inexpensive retrieval of accessible server-rendered content, with guarded escalation when a detectable gap requires JavaScript. Source verification and explicit failure outcomes are useful engineering properties. This experiment does not establish universal coverage, production throughput, a best possible Crawlee/Crawl4AI configuration, or accurate extraction across the entire web.
