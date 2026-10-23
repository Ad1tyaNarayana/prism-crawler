"""Rebuild the five-site diagnosis from local receipts; makes no network requests."""

import gzip
import hashlib
import json
import shutil
from pathlib import Path
from statistics import median

from markdown_it import MarkdownIt

from benchmarks.provider_live import quality_review
from benchmarks.real50 import SiteCase, runtime_digest

ROOT = Path('reports/access-five')
IDS = ('britannica', 'smithsonian', 'britishmuseum', 'bookshop', 'apnews')
NAMES = ('Britannica', 'Smithsonian', 'British Museum', 'Bookshop', 'AP')
PIN = 'a779ddac4316a2b82522116cee70c6e32046f9ca'
GITHUB = f'https://github.com/firecrawl/firecrawl/blob/{PIN}/'


def read(path):
    return json.loads(path.read_text())


def verify_body(path, digest):
    assert hashlib.sha256(gzip.decompress(path.read_bytes())).hexdigest() == digest, path


def main():
    phase = read(ROOT / 'paired/phase.json')
    assert phase['cases'] == list(IDS)
    assert phase['runtime_sha256'] == runtime_digest()
    assert (ROOT / 'paired/manifest.json').read_bytes() == Path('benchmarks/real50.json').read_bytes()
    assert (ROOT / 'paired/quality-contract.json').read_bytes() == Path('benchmarks/real50-review.json').read_bytes()
    cases = {c['id']: SiteCase(**c) for c in read(Path('benchmarks/real50.json'))}
    rows, historical = [], []
    for ident, name in zip(IDS, NAMES, strict=True):
        local = {}
        for method in ('adaptive', 'native'):
            receipt = ROOT / f'paired/{method}-{ident}-0.json'
            record = read(receipt)
            assert record['url'] == cases[ident].url
            assert not record['readable_check']['passed']
            assert record['document']['status'] == 403
            assert record['document']['outcome'] == 'access_wall'
            assert len(record['captures']) == 2  # One policy and one target response.
            for capture in record['captures']:
                verify_body(ROOT / 'paired/bodies' / (capture['body_sha256'] + '.gz'), capture['body_sha256'])
            page = next(c for c in record['captures'] if c['role'] != 'robots')
            if method == 'native':
                assert page['response_evidence']['headers']['cf-mitigated'] == 'challenge'
                assert not any(e.get('role') == 'asset' and e.get('allowed') for e in record['events'])
                assert not any(e.get('from_disk_cache') or e.get('from_service_worker') for e in record['events'])
                assert not record['document'].get('metadata', {}).get('interception_errors')
            local[method] = {'elapsed_ms': record['elapsed_ms'], 'status': 403,
                             'evidence': page['response_evidence'],
                             'receipt': str(receipt.relative_to(ROOT))}
        provider = []
        for receipt in sorted(Path('reports/provider-live50').glob(f'run-*/firecrawl-{ident}.json')):
            record = read(receipt)
            digest = record['receipt']['response_sha256']
            body = receipt.parent / 'bodies' / (digest + '.gz')
            if not body.exists():
                body = receipt.parent.parent / 'bodies' / (digest + '.gz')
            verify_body(body, digest)
            response = json.loads(gzip.decompress(body.read_bytes()))
            metadata = response['data']['metadata']
            checked = quality_review(cases[ident], record['document'])
            row = {'id': ident, 'receipt': str(receipt), 'response_sha256': digest,
                   'started_at': record['receipt']['started_at'],
                   'elapsed_ms': record['receipt']['elapsed_ms'],
                   'metadata': {k: metadata.get(k) for k in ('sourceURL', 'url', 'statusCode', 'proxyUsed', 'cacheState')},
                   'strict_passed': checked['readable_check']['passed'],
                   'missing': checked['readable_check']['missing']}
            assert row['metadata']['statusCode'] == 200
            assert row['metadata']['proxyUsed'] == 'basic'
            provider.append(row)
        assert len(provider) == 5
        historical.extend(provider)
        rows.append({'id': ident, 'name': name, 'url': cases[ident].url, 'local': local,
                     'firecrawl_historical': {'responses_200': 5,
                        'strict_passes': sum(p['strict_passed'] for p in provider),
                        'median_ms': median(p['elapsed_ms'] for p in provider),
                        'final_urls': sorted({p['metadata']['url'] for p in provider})}})
    ap = read(ROOT / 'ap-final-url/result.json')
    assert ap['document']['status'] == 403 and ap['document']['outcome'] == 'access_wall'
    for capture in ap['captures']:
        verify_body(ROOT / 'ap-final-url' / (capture['body_sha256'] + '.gz'), capture['body_sha256'])
    assert not any(e.get('role') == 'asset' and e.get('allowed') for e in ap['events'])
    evidence = {'rows': rows, 'historical_provider_receipts': historical,
                'fresh_local_observations': 10, 'original_targets_recovered': 0,
                'supplemental_ap_final_url': ap, 'vendor_calls': 0,
                'verified': {'response_hashes': True, 'original_urls_and_checks': True,
                             'native_allowed_assets': 0, 'production_runtime_unchanged': True}}
    (ROOT / 'comparison.json').write_text(json.dumps(evidence, indent=2))
    table = '\n'.join(
        f"| {r['name']} | {r['local']['adaptive']['elapsed_ms']/1000:.2f}s / 403 | "
        f"{r['local']['native']['elapsed_ms']/1000:.2f}s / 403 | "
        f"{r['firecrawl_historical']['strict_passes']}/5 |"
        for r in rows)
    report = f"""# Five access failures: what Firecrawl does differently

**No original target was recovered: 0/5 for adaptive, 0/5 for native Chromium.** This is a fresh, focused ten-observation run on the five original URLs, plus one separate AP final-URL check. No paid API calls, external proxies, new dependencies or access-wall handling were used. The production crawler was not changed: the evidence did not justify a retrieval fix.

| Site | Fresh adaptive time/status | Fresh native time/status | Historical Firecrawl strict passes |
| --- | --- | --- | --- |
{table}

Timings include local client startup, robots, target request and cleanup. Each method has one fresh observation per site, alternating method order. These are refusal times, not useful-content latency estimates. There are no confidence intervals or speedup claims. Firecrawl's column is explicitly historical, five earlier repetitions; no fresh provider benchmark was performed.

## The failure is now identified more precisely

Every native target response is HTTP 403 with `cf-mitigated: challenge`. Cloudflare [documents this header as its explicit challenge signal](https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/detect-response/). Smithsonian uses a branded request-verification page but carries the same header. Four adaptive responses carry it too. Bookshop's adaptive response instead contains a Cloudflare block template; changing to native Chromium changes the refusal type, not the retrieval outcome.

All ten original-target observations received a readable 200 robots file and then one refused target response. Native challenge documents were stopped before scripts ran; there were zero allowed asset requests. DNS/TLS/HTTP worked well enough to receive a server response. There was no article body for a parser, model, longer render wait or scroll loop to recover. This identifies the failure stage and response mechanism; it does **not** identify the exact server rule or explain which property lets Firecrawl through.

## What the public Firecrawl source actually establishes

Source was inspected at commit `{PIN}`. Files are preserved in [firecrawl-source/](firecrawl-source/) and were not executed or copied into our crawler. The snapshot is not proof of the deployed cloud version used by the historical receipts.

1. **Multiple engines.** The [engine registry]({GITHUB}apps/api/src/scraper/scrapeURL/engines/index.ts#L59) includes fetch, Playwright, managed Chrome CDP, TLS client and enhanced variants. Our native experiment addresses ordinary browser networking; it does not recreate the managed service.
2. **Optional external infrastructure.** [safeFetch]({GITHUB}apps/api/src/scraper/scrapeURL/engines/utils/safeFetch.ts#L68) selects either an ordinary Undici Agent or ProxyAgent depending on configuration. The [Playwright service]({GITHUB}apps/playwright-service-ts/api.ts#L67) similarly has optional upstream-proxy configuration behind a local SSRF guard. A loopback safety proxy alone does not change external network location.
3. **Site-protection escalation.** [The Fire Engine client]({GITHUB}apps/api/src/scraper/scrapeURL/engines/fire-engine/scrape.ts#L271) handles a backend `retryWithStealth` signal. [Its tests]({GITHUB}apps/api/src/scraper/scrapeURL/engines/fire-engine/__tests__/siteProtection.test.ts#L85) explicitly distinguish normal retry handling from stopping when Safe Mode disables site handling. Current [enhanced-mode documentation](https://docs.firecrawl.dev/features/enhanced-mode) describes one enhanced-proxy retry after 401/403/429. This is documented capability, not proof of use on any of these five.
4. **Different identity/session defaults.** The bundled [Playwright service]({GITHUB}apps/playwright-service-ts/api.ts#L175) uses a generated browser user agent unless overridden, and [fetch uses a cookie jar]({GITHUB}apps/api/src/scraper/scrapeURL/engines/utils/safeFetch.ts#L131). Our native test keeps our explicit crawler identity and permits only a fresh ephemeral context. These differences are candidates, not isolated causes; we did not impersonate a browser identity or import sessions.
5. **The constraint settings matter.** Public [Safe Mode configuration]({GITHUB}apps/api/src/lib/safe-mode.ts#L85) contains separate controls for robots, site handling, browser identity and proxy escalation. The saved benchmark does not record resolved organization controls. We therefore cannot claim Firecrawl succeeded under the same no-bypass conditions as this local test. [Firecrawl's deployment guide](https://docs.firecrawl.dev/contributing/open-source-or-cloud) also distinguishes default self-hosting from managed advanced services.

The visible advantage is a broader retrieval stack. Exact per-site cloud engine, outbound address, request headers, cookies, server-side decision, and retry trace are absent from our receipts. Without that information, attributing the win specifically to IP reputation, geography, fingerprinting or challenge handling would be speculation.

## Correction to the earlier proxy inference

All 25 saved Firecrawl responses on these five sites contain `proxyUsed: basic`. That is **not proof of proxy use**: the public [fetch engine returns that label unconditionally]({GITHUB}apps/api/src/scraper/scrapeURL/engines/fetch/index.ts#L216), while its dispatcher can be direct. The earlier failure-focus report overstated what this label established. This report supersedes that interpretation; historical raw evidence remains unchanged.

All 25 saved responses had status 200 and substantive output. Only 20/25 pass the original frozen content checks: British Museum misses the required `history` term in all five. Retrieval success and passing a topic checklist are separate claims. No frozen checks were relaxed here.

## Fix hypotheses tested, and what failed

| Hypothesis | Bounded test | Result / decision |
| --- | --- | --- |
| Native browser networking fixes the response | Same five URLs, machine, honest identity and policy, fresh adaptive/native clients | 0/5 recovered in either; native transport alone is insufficient here. |
| AP's final URL avoids a stale entry point | One separate native request to `https://apnews.com/science`, the final URL in all five provider receipts | 403 Cloudflare challenge in {ap['elapsed_ms']/1000:.2f}s; no rewrite installed and no original-URL recovery credited. |
| Parser/readiness changes can recover missing text | Inspect returned bodies, status, explicit challenge header and request events | Refusal occurs before article content. No parser or wait changes justified. |
| Missing failure evidence makes diagnosis unreliable | Add an allowlisted header/template classifier to the benchmark | Fixed diagnostic gap; known challenge responses are now distinguished without guessing their triggering rule. |

No legitimate local content-recovery fix was established by these tests. Repeated retries, hiding identity, changing network location to evade a refusal, or executing challenge scripts would contradict this task's boundary. We stopped each attempt at the access response. An owner-authorized feed/API/export or permission for our crawler would be a separate access route, not a claim that these original pages now work.

## Reverse-engineering skill review

The online [reverse-engineer skill listing](https://www.skills.sh/rmyndharis/antigravity-skills/reverse-engineer) primarily describes compiled-binary tools such as IDA, Ghidra and disassemblers. It was reviewed as a reference, not installed or executed. It cannot reveal an unavailable cloud service's internals. Source inspection plus bounded black-box observations was the relevant method here.

## What changed and how it was verified

Added `benchmarks/access_diagnostics.py`, integrated evidence capture into `benchmarks/native12.py`, and added four diagnostic tests. Sixteen focused tests passed in 2.34 seconds, covering diagnostic classification, ordinary native rendering, wall-script prevention, robots, scope and method guards. Ruff passes on all changed benchmark/test files. No production signatures, dependencies or access decisions changed.

[Machine-readable comparison and historical receipt references](comparison.json), [validation](validation.json), [fresh paired receipts](paired/), and [AP final-URL evidence](ap-final-url/result.json) preserve the distinction between live local tests and saved vendor results. Response hashes, original URLs and frozen checks were verified. The reporter makes no network calls.

Rerun only these five with a fresh output path:

```sh
.venv/bin/python -m benchmarks.native12 --output reports/access-five/new-run --case-ids britannica smithsonian britishmuseum bookshop apnews
```
"""
    (ROOT / 'analysis.md').write_text(report)
    rendered = MarkdownIt('commonmark', {'html': False}).enable('table').render(report)
    (ROOT / 'index.html').write_text('''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Five access failures — source audit</title>
<style>body{font:17px/1.65 system-ui;color:#243246;background:#f4f6f8;margin:0}main{max-width:1080px;margin:40px auto;background:white;padding:36px;border:1px solid #dde3e9;border-radius:14px}h1,h2{line-height:1.25;color:#152439}h2{margin-top:2em}a{color:#175fb0}table{border-collapse:collapse;width:100%;font-size:15px}td,th{padding:12px;border:1px solid #dce3eb;text-align:left}th{background:#eaf0f6}pre{overflow:auto;background:#eff3f7;padding:16px}code{font-size:.86em;overflow-wrap:anywhere}@media(max-width:700px){main{margin:0;padding:18px}table{font-size:12px}td,th{padding:6px}}</style><main>''' + rendered + '</main></html>')
    shutil.copyfile(__file__, ROOT / 'source/benchmarks/access_five_report.py')
    files = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in ROOT.rglob('*') if p.is_file() and p.name != 'artifacts-manifest.json'}
    (ROOT / 'artifacts-manifest.json').write_text(json.dumps(files, indent=2, sort_keys=True))
    print(json.dumps({'original_recovered': 0, 'fresh_original_observations': 10,
                      'historical_provider_strict_passes': sum(p['strict_passed'] for p in historical),
                      'verified_artifacts': len(files)}))


if __name__ == '__main__':
    main()
