# Implementation limits

## Supported behavior

- A persistent URL frontier with one active page per origin and concurrent work across origins.
- HTTP and HTTPS, with explicit origin scope. Tracking parameters and ordinary fragments are removed from URL identity.
- Robots rules through Protego, including allow/disallow precedence and crawl delays. Missing robots files permit access. Unavailable robots files cause a conservative refusal with a distinct outcome.
- Adaptive pacing, response-size limits, retry limits, Retry-After handling, request budgets, page budgets, frontier bounds, and run deadlines.
- DNS resolution that rejects non-global addresses before connecting. Literal private IPs are rejected too. `--allow-private` is an explicit option for trusted local fixtures.
- Chromium CDP Fetch requests fulfilled through the guarded HTTP transport. A local deny-only proxy, with loopback bypass disabled, blocks native browser egress from uninstrumented targets. Cookies and authorization headers are not forwarded. Website POST requests, WebSockets, media, fonts, and unlisted origins are blocked. Stylesheets load by default; `--no-load-styles` skips them. Service workers are disabled. Pending asset requests are cancelled and joined before storage closes, and terminal protocol actions finish before cancellation propagates.
- Independent browser and HTTP timeouts, a configurable JavaScript settling window, and a 16 MB default decoded-body and rendered-DOM limit. The page budget begins after browser-slot acquisition; the total crawl deadline still bounds queueing. Explicit lower limits still reject oversized content.
- Browser Origin, Referer and Accept headers survive guarded transport for legitimate CORS behavior. HEAD stays HEAD. Robots redirects can use an already authorized page or asset origin, while excluded, private and unlisted destinations remain denied.
- Browser metadata records dependency refusals, page errors, pending requests and deadline exhaustion. Favicon HEAD probes use the same robots and rate-limited transport as other requests.
- Text extraction from HTML, tables, repeated records, JSON-LD, hydration JSON, textual JSON responses, RSS, and Atom. Serialized component state is not accepted as prose. Partial structured descriptions lose priority when credible fuller document candidates exist. Equation-image and MathML alternative text is preserved.
- Pages containing several independent article sections and no main container can use their cleaned whole-body content. Existing main containers retain precedence. This addresses fragmented staff and pricing pages without site-specific text or selectors.
- Open shadow-root content is serialized by the browser. Snapshots are bounded to 50,000 nodes and depth 100; exceeding either limit fails the snapshot instead of silently truncating it.
- HTTP header lines and fields are bounded at 64 KiB, allowing large valid security-policy headers that otherwise prevented reading robots files.
- Bounded sitemap traversal, including nested and gzip-compressed sitemap files.
- Exact duplicates by SHA-256 and near-duplicates by a 64-bit SimHash with indexed bands. Near-duplicate matching is heuristic and does not suppress the original document.
- HTTP validators, per-page revisit intervals, interrupted-run recovery, and persistent route observations.

## Boundaries that matter

This is a single-machine research system, not a distributed crawling service. SQLite and a POSIX file lock enforce one writer process per database. There is no queue broker, autoscaler, authentication server, tenant isolation, secret manager, or production deployment.

The crawler does not solve CAPTCHAs, rotate deceptive fingerprints, bypass authentication, log into accounts, or circumvent paywalls. A visible wall or an HTTP access restriction is a refusal. It does not interpret legal terms automatically. Use `--exclude-origin` when a site's terms or your agreement disallow crawling.

Some legitimate applications need cross-origin APIs, cookies, POST-based GraphQL requests, service workers, user interaction, scrolling, or much longer waits. Those paths are outside the verified scope. Explicitly listed asset origins allow GET dependencies but do not authorize new page scope. A script-driven navigation that subsequently returns an HTTP redirect is reported as unsupported. Normal HTTP redirects are resolved before rendering.

Browser cancellation events do not reliably arrive before an intercepted request is sent. An attempted queue-cancellation optimization failed its regression and was removed. Obsolete queued assets can consume request budget. Out-of-process frames or workers without an attached interceptor fail through the local proxy instead of receiving unrestricted native network access.

The Windy text profile allows three observed dependency origins and verifies live forecast values. Basemap and label tiles remain outside that profile. It is not a complete visual map renderer. The separate functional check does not replace the original benchmark's literal body-text marker.

Bounded text PDF extraction runs in a disposable subprocess with byte, page, text, CPU and wall-time limits. Encrypted, image-only and malformed PDFs are unsupported; there is no OCR. Office-document, image, audio and video extraction are not implemented. JSON extraction recognizes textual content fields rather than recovering an arbitrary business schema. Complex embedded application state may need a site-specific adapter. Hash-based client-side routing is not supported because fragments are removed during canonicalization.

The quality score is a heuristic. It can miss meaningful short content, retain promotional text, or accept partial extraction. The live checks use content markers and selected reviewed requirements, not complete human-labelled page text. The 50-site study tests one URL per site, and its post-fix rerun uses development data. Passing these checks does not establish broad web accuracy or site-wide completeness.

The learned router groups observations by origin and path pattern. It needs repeated examples, expires observations after seven days, and rechecks HTTP periodically. It does not solve a global optimization problem or calculate a dollar price for CPU time. Re-crawl intervals respond to observed changes but do not estimate unseen changes between visits.

Model probabilities are not calibrated for this crawler's workload. A threshold that worked on a few development cases is not an error guarantee. Shadow mode records advice without acting on it. Model assistance remains optional. Public site content is untrusted input even when a local decision model reads it.

Robots caches last for one run. Long-lived `recrawl --loop` starts fresh runs and refreshes policy. Raw content may contain personal data from public pages. Exports retain what the source returned and do not automatically redact it.

The network restrictions have behavioral tests, including origin-side receipts proving that forbidden fixture requests did not arrive. They are not an independent security audit or an OS-level egress sandbox. Do not expose this crawler as an untrusted multi-tenant service without further isolation and review.
