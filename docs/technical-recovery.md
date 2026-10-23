> Historical research note. For current results and the supported adaptive path, see [evaluation](evaluation.md) and [architecture](architecture.md). Raw reports referenced below stay local and are intentionally excluded from the source repository.

# Technical recovery and the same 50-site rerun

750 final page attempts on the unchanged 50-site set, plus 75 final Exa website attempts. Every strategy ran five times. A complete earlier 825-attempt technical round is preserved in `reports/technical`; it revealed the Exa failures and preceded the actual CLI queueing fix. All crawling ran locally with model inference disabled.

| Strategy | Earlier reviewed checks | Current reviewed checks | Median batch time, range | Median requests | Median renders |
| --- | --- | --- | --- | --- | --- |
| HTTP only | 29/50 | 30/50 | 46.9 s (39.5 to 52.5) | 99 | 0 |
| Browser first | 30/50 | 32 to 33/50 | 256.0 s (227.3 to 288.4) | 827 | 35 |
| Adaptive | 30/50 | 32 to 33/50 | 90.6 s (79.4 to 129.6) | 374 | 4 |

New original-check passes: archive, openstax, edx. Fewer passes than the historical study: elpais-es.

## Priority-site functional checks

These checks supplement the original results and do not alter their scores.

| Site | HTTP | Browser | Adaptive |
| --- | --- | --- | --- |
| archive | 0/5 | 5/5 | 5/5 |
| openstax | 0/5 | 5/5 | 5/5 |
| windy | 0/5 | 5/5 | 5/5 |
| edx | 5/5 | 5/5 | 5/5 |

## What changed and what did not work

Archive and OpenStax needed more time to finish their politely paced JavaScript dependencies. CSS is now loaded because application loaders can wait for it. Browser attempts have a separate 90-second budget; individual HTTP requests still time out after 15 seconds in this comparison. The settling window now controls how long a JavaScript page must stay stable.

edX sends more than 4 MB of decoded HTML. The bounded response limit is now 16 MB, shared by all strategies. Explicit lower byte limits still work.

Windy required three explicitly configured asset origins: node.windy.com, account.windy.com, and ims.windy.com. All use the guarded transport and their robots rules. A robots redirect to an already authorized origin is now supported. Browser requests retain safe Origin and Referer headers for CORS, and HEAD stays HEAD. Cookies and authorization headers are not forwarded.

Playwright's routing layer automatically aborts favicon requests, including Windy's HEAD connectivity check. Chromium CDP Fetch interception now fulfills that request through the guarded transport. A local deny-only proxy blocks native browser egress from any uninstrumented target. No Fetch.continueRequest path is used. The offline banner disappeared and actual forecast values arrived. Map label and basemap tiles remain outside this text-crawling profile, so this does not claim full visual map rendering.

MusicBrainz returned a browser-verification wall during the pilot and was readable again in the first measured run. Verification pages are refused before executing their script. This variation is retained in the results rather than attributed to the parser.

El País was readable in earlier runs but returned HTTP 403 during the final repetition series. Those responses are retained as access refusals. Passing counts can therefore vary across repetitions even with frozen code. A failed example is shown in the site table whenever any repetition fails, rather than hiding it behind a successful first run.

The original Windy check still requires the literal word 'forecast' in body text. The recovered UI exposes temperatures and wind speeds, while that word is in the page title. Its original score is retained as a failure. The separate functional check requires visible weather values, a successful forecast response and HEAD probe, and no offline message. It is a development check, not a replacement for the frozen metric.

The actual CLI also exposed a queueing bug: with the default two browser slots, OpenStax lost part of its 90-second budget before acquiring a slot. The attempt deadline now starts after slot acquisition; the total crawl deadline still bounds the run. A regression sends three slow applications through one browser slot. Credible structured-data pages with very short visible titles also finish promptly instead of burning the rendering deadline. Both repairs were made before the final source freeze and complete rerun.

The favicon behavior is documented in the [Playwright interception test](https://github.com/microsoft/playwright/blob/main/tests/page/page-request-intercept.spec.ts). The replacement uses the [Chromium Fetch protocol](https://chromedevtools.github.io/devtools-protocol/tot/Fetch/). Actual local regression receipts and live diagnostics are retained in `.audit/` and `reports/technical/development/`.

## Exa website tests

Five public pages were selected from links on the homepage before the supplemental runs: homepage, About, Pricing, documentation introduction, and Contents quickstart. These are website extraction tests. They do not test or consume the Exa search API. The final manifest and raw outputs are under `reports/technical-final/exa`.

| Strategy | Pages passing, each run | Median five-page batch | Median requests |
| --- | --- | --- | --- |
| HTTP only | 5/5 | 3.3 s | 6 |
| Browser first | 5/5 | 11.7 s | 20 |
| Adaptive | 5/5 | 3.3 s | 6 |

| Exa page | HTTP passing repetitions | Browser | Adaptive |
| --- | --- | --- | --- |
| exa-home | 5/5 | 5/5 | 5/5 |
| exa-about | 5/5 | 5/5 | 5/5 |
| exa-pricing | 5/5 | 5/5 | 5/5 |
| exa-docs | 5/5 | 5/5 | 5/5 |
| exa-contents | 5/5 | 5/5 | 5/5 |

The first technical round exposed new extraction failures. About returned one 454-character staff biography from a 10,955-character cleaned document. Pricing returned a 522-character enterprise section from 4,880 characters. Both failed all three strategies. Browser-first also chose a 796-character demo fragment on the homepage. HTTP and adaptive passed 3/5 pages; browser-first passed 2/5, consistently across five repetitions. The saved HTML contained the missing content. The shared parser mistook an isolated article section for complete coverage. It now considers the cleaned whole page when there are multiple independent article sections and no main container; an existing main container retains precedence. The final scores reported here come from fresh live runs after that general repair, with the original Exa checks unchanged. Exa is now a development set, not an unseen test. These remain bounded content checks, not proof of complete-page extraction.

The first-round offline diagnosis is saved in `reports/technical/exa/diagnosis.json`, including body hashes, candidate sizes and the missing markers found in the complete saved document. It makes no network requests. The first round and final live measurements remain separate.

## Remaining original-check failures

| Site | Example failing outcome | Passing repetitions | Reason |
| --- | --- | --- | --- |
| britannica | access_wall | 0/5 | HTTP 403 access restriction; Missing marker: internet, network, computer |
| noaa | robots_denied | 0/5 | Robots unavailable or restricted, HTTP 403; Missing marker: climate, temperature, water / ocean |
| usgs | access_wall | 0/5 | Visible access or authentication wall; Missing marker: water, cycle, evaporation / precipitation |
| mayoclinic | robots_denied | 0/5 | Robots unavailable or restricted, HTTP 403; Missing marker: cold, symptom, virus / viral |
| nature | out_of_scope | 0/5 | Origin is outside the explicit crawl scope; Missing marker: nature, research, journal |
| metmuseum | robots_unavailable | 0/5 | Robots unavailable or restricted, HTTP 429; Missing marker: met, art, museum |
| smithsonian | access_wall | 0/5 | HTTP 403 access restriction; Missing marker: smithsonian, museum, research |
| britishmuseum | access_wall | 0/5 | HTTP 403 access restriction; Missing marker: museum, collection, history |
| ikea | ok | 0/5 | Redirected to a different target |
| patagonia | access_wall | 0/5 | Visible access or authentication wall; Missing marker: patagonia, environment / impact, material / supply |
| bookshop | access_wall | 0/5 | HTTP 403 access restriction; Missing marker: book, independent, shop / store |
| reuters | robots_denied | 0/5 | robots.txt disallows this URL; Missing marker: world, reuters, news |
| apnews | access_wall | 0/5 | HTTP 403 access restriction; Missing marker: science, space / research / scientist |
| elpais-es | access_wall | 4/5 | HTTP 403 access restriction; Missing marker: ciencia, investig / científic / cientific |
| nhk-ja | error | 0/5 | TimeoutError: ; Missing marker: nhk / ＮＨＫ, ニュース |
| khanacademy | access_wall | 0/5 | Visible access or authentication wall; Missing marker: algebra, equation, function |
| windy | ok | 0/5 | Missing marker: forecast |
| lobsters | robots_denied | 0/5 | robots.txt disallows this URL; Missing marker: comment, point |

## Limits and evidence

The same 50 URLs and original reviewed requirements are unchanged. This is a development rerun, not a new held-out evaluation. Each strategy ran five times, with rotated strategy and seed order, fresh databases and browsers, four workers, four browser slots, 500 ms minimum delay per origin, a 1,500-request budget, one retry and no model calls. Sitemaps and link traversal are disabled. These cold starts do not measure long-term learned routing or recrawl accuracy. The new resource profile is identical across strategies, but differs from the earlier 4 MB / shorter-browser-budget profile. Before and after timing is therefore not a controlled speedup measurement. Ranges and request counts are reported alongside failures. Local request telemetry is not an independent server receipt.

Nature still enters an out-of-scope identity and cookie flow. IKEA's requested product still redirects to a category page. NHK completes TLS but returns no HTTP/1.1 bytes before timeout; an independent curl probe also failed. Access walls, robots refusals and unavailable robots files remain refusals. No CAPTCHA solving, login, stealth user agent, paid proxy, Exa API call or hosted model was used.

The attempted optimization to cancel all abandoned browser requests before transmission failed its regression and was removed. Queued obsolete requests can still consume budget. Pilot, failed development experiments, source snapshots, response bodies and decision logs are retained. Review was inline under the user's no-delegation instruction; this is not an independent security audit.

## Verification beyond benchmark scores

All 63 automated tests pass. The final local fixture uses the normal 90-second browser budget: HTTP recovers 15/18 content cases, and browser-first and adaptive recover 18/18. All three pass 4/4 policy checks, with zero forbidden requests at the fixture server and matching request receipts. The actual CLI, using its default two browser slots, exports verified content for Archive, OpenStax, Windy and Exa's homepage.

The first CLI attempt failed on OpenStax because queue time consumed its render budget. The first default-budget fixture run also timed out on structured-only pages; a shorter-budget diagnostic separated that waiting cost from functional correctness. Both failed runs, the diagnostic rerun and the final passing checks are retained. The final fixture passes without the shorter test-only budget.

An additional adaptive run was profiled separately and excluded from every comparison score and timing. The main event-loop thread profile spans 98.4 seconds; 89.0 seconds were spent in polling. That includes waiting for network activity, politeness delays, the browser and parser worker threads. It does not aggregate worker-thread CPU time and is not a measurement of network latency alone. See profile-measurement.json and profile-summary.txt for the trace.

The original 43 tests remain; 20 additional cases cover the new transport, budgets, CORS, HEAD, robots redirects, native egress, settling, access-wall preflight, queueing and extraction coverage. Source snapshots and compressed bodies are verified by `benchmarks.verify_technical`. The machine-readable summary is `comparison.json`; `results.csv` lists every site and strategy.

Runtime SHA-256: `6da120a9637ba55184c8b142fd53d0466fdc41d8d20dc58ac20cef3a4fc1a5c7`.
50-site manifest SHA-256: `873e07ed704edf4c58b3136793225e3d95e9ab92b86e2400786ec84ad4bb798f`.
Original review SHA-256: `0075d55b5177e21b1ffacb623fae19152ebf0310e30b8d7734b0077ee78cd952`.

Repeat the comparison with `.venv/bin/python -m benchmarks.real50 --phase after --repetitions 5 --output-root reports/technical-recheck --profile benchmarks/technical-profile.json`. Completed measured results are never overwritten. Rerun Exa with `.venv/bin/python -m benchmarks.exa --output reports/exa-recheck`.

The local interactive report is at http://127.0.0.1:8788/technical-final/.
