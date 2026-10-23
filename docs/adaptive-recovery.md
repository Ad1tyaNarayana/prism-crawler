> Historical research note. For current results and the supported adaptive path, see [evaluation](evaluation.md) and [architecture](architecture.md). Raw reports referenced below stay local and are intentionally excluded from the source repository.

# Adaptive crawler: all 17 remaining failures investigated

The product now has one supported path: HTTP and advertised data first, guarded Chromium when needed, with source verification and bounded recovery. Historical baseline modes remain hidden compatibility options. No paid service, proxy, model call, CAPTCHA solving or access-wall bypass was used.

The prior reviewed score was **33/50**. Three final adaptive runs on the **same 50 URLs and unchanged content/review checks** scored **35 / 35 / 34 out of 50**. Recovered targets: **nature, windy**. The denominator remains 50.

The final profile explicitly authorizes https://idp.nature.com for public redirect hops and its robots check. It retains the earlier Windy asset origins. This configured permission is material: Nature is not recovered by automatically trusting a related hostname. Default scope remains strict.

## Breakdown of the original 17

| Category | Count | Targets |
| --- | --- | --- |
| access_wall | 8 | britannica, usgs, smithsonian, britishmuseum, patagonia, bookshop, apnews, khanacademy |
| explicit_robots | 2 | reuters, lobsters |
| unreadable_robots | 3 | noaa, mayoclinic, metmuseum |
| redirect_helper | 1 | nature |
| wrong_item | 1 | ikea |
| transport | 1 | nhk-ja |
| title_preservation | 1 | windy |

## Every original failure, before and after

| Target | Before | After, 3 runs | Explanation |
| --- | --- | --- | --- |
| britannica | access_wall | 0/3 content checks; access_wall (3) | HTTP 403 verification wall. |
| noaa | robots_denied | 0/3 content checks; robots_unavailable (3) | Robots returns HTTP 403. Policy unreadable, not an explicit parsed disallow. Content is not requested. |
| usgs | access_wall | 0/3 content checks; access_wall (3) | HTTP 202 robot-verification page; running its challenge is excluded. |
| mayoclinic | robots_denied | 0/3 content checks; robots_unavailable (3) | Robots returns HTTP 403. Policy unreadable, not an explicit parsed disallow. Content is not requested. |
| nature | out_of_scope | 3/3 content checks; ok (3) | Recovered through an explicitly authorized public GET redirect helper, with its own robots check. No cookies or credentials used. |
| metmuseum | robots_unavailable | 0/3 content checks; robots_unavailable (3) | Robots returns HTTP 429. Bounded retries now respect Retry-After, but the live policy file remains unavailable. |
| smithsonian | access_wall | 0/3 content checks; access_wall (3) | HTTP 403 request-verification wall. |
| britishmuseum | access_wall | 0/3 content checks; access_wall (3) | HTTP 403 verification wall. |
| ikea | incomplete | 0/3 content checks; incomplete (3) | Original product redirects to a category; captured category source contains zero occurrences of item 00263850. It is not substituted. |
| patagonia | access_wall | 0/3 content checks; access_wall (3) | HTTP 200 queue/verification page instead of the footprint article. |
| bookshop | access_wall | 0/3 content checks; access_wall (3) | HTTP 403 blocked response. |
| reuters | robots_denied | 0/3 content checks; robots_denied (3) | A parsed robots rule disallows this URL. |
| apnews | access_wall | 0/3 content checks; access_wall (3) | HTTP 403 verification wall. |
| nhk-ja | error | 0/3 content checks; error (3) | No usable response from this machine. HTTP/1.1 fails at 15 and a configured 45 seconds; independent HTTP/2 returns a stream INTERNAL_ERROR after verified TLS. |
| khanacademy | access_wall | 0/3 content checks; access_wall (3) | HTTP 200 Client Challenge instead of algebra content. |
| windy | ok | 3/3 content checks; ok (3) | Recovered by preserving the actual source title in extracted text. The unchanged body-marker check and separate live-forecast functional check both pass. |
| lobsters | robots_denied | 0/3 content checks; robots_denied (3) | A parsed robots rule disallows this URL. |

## Engine changes

An explicit redirect-origin permission now allows public helper hops without adding them to the frontier or authorizing arbitrary initial requests. Helper robots, DNS/public-address validation, exclusions, request/byte limits and GET/HEAD restrictions still apply. Nature follows about → journal-information → authorized helper → public journal-information. The server returns the article with cookies_not_supported in its query. The actual main content is extracted; no cookie jar, credential or challenge execution is enabled.

Robots 429/5xx and transport failures now get bounded retries through the same per-origin scheduler. Retry-After is respected. Persistent failures still prevent content fetches. Robots 401/403 is correctly reported as robots_unavailable instead of pretending a parsed disallow rule exists. Explicit parsed rules remain robots_denied.

Source titles are preserved consistently in all accepted candidate text before verification and optional candidate selection. Quality scores are not increased by adding a title. Windy already supplied working forecast data, but its literal forecast word existed in the actual title. The exported document now retains it. No Windy-specific keyword is inserted and the old metric is unchanged.

Network failures now record their phase, budget, error class and OS errno when available. The HTTP/2 investigation did not repair NHK, so no unproven extra transport dependency was added.

## Results and limits

| Final run | Reviewed content | Requests | Chromium pages |
| --- | --- | --- | --- |
| 1 | 35/50 | 379 | 4 |
| 2 | 35/50 | 379 | 4 |
| 3 | 34/50 | 379 | 4 |

Additional failures outside the original 17 are retained below. They are not hidden by the earlier successful runs.

| Run | Additional target | Outcome | Reason |
| --- | --- | --- | --- |
| 3 | elpais-es | access_wall | HTTP 403 access restriction |

Nature helper authorization is a configuration change; title preservation, retries and diagnostics are engine changes. These runs use a repeated development set, not unseen websites. Changes in coverage are not a statistical speed or generalization claim.

Eight access walls, two explicit robots exclusions and three unreadable robots files cannot be converted into extracted content under the current no-bypass constraints. Repeated requests may succeed later if the site changes its policy or availability; current results remain refusals. IKEA does not return the specified product. NHK does not return a usable response from this machine through the tested permitted transports. No alternative site, different product or cached provider result is presented as the original target.

All 90 automated tests pass. Nine added cases cover transient-policy retries, unreadable-policy classification, request refusal, helper authorization, helper robots, helper frontier isolation, title preservation and actionable timeouts. Retained bodies are hash-verified. These are local tests and inline review, not an independent security audit.

The eight access pages were inspected directly. HTTP 200 is not taken as proof of content: USGS returns a robot check at 202, while Patagonia and Khan Academy return queue/challenge pages at 200. All remain terminal refusals.

## Working example

```sh
cd /Users/aditya/Me/optimal-crawler
bash scripts/demo-adaptive.sh
```

The demo uses the same explicit helper and dependency permissions and exports actual Nature and Windy documents. It requires no key.

## Evidence

- [All 17 machine-readable outcomes](../reports/adaptive/comparison.json).
- [All 17 as CSV](../reports/adaptive/remaining17.csv).
- [Current-source reproduction before fixes](../reports/adaptive/before17/run-0.json).
- [Final run 1](../reports/adaptive/final50/run-0.json), [run 2](../reports/adaptive/final50/run-1.json), [run 3](../reports/adaptive/final50/run-2.json).
- [Guarded transport diagnostics](../reports/adaptive/diagnostics/summary.json), [longer NHK request](../reports/adaptive/nhk-long/result.json).
- [Final authorization profile](../reports/adaptive/final50/profile.json), [source fingerprint](../reports/adaptive/final50/phase.json).

The HTTP/2 diagnostic used a public-IP-pinned curl request only after the crawler allowed the URL through robots. It used verified TLS, the same crawler user agent, no proxy and no redirect following. The [HTTP/2 protocol documentation](https://www.encode.io/httpcore/http2/) describes ordinary negotiated HTTP/2; changing protocol did not resolve the observed NHK stream error.

Final source SHA-256: `d241fe2ccbcb009a2e333016968884e51561fd9d58a68c894e5598888d653806`.
