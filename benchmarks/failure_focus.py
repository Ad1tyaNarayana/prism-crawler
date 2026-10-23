"""Collate failed sites without changing frozen benchmark scores or fetching the web."""

import argparse
import csv
import gzip
import hashlib
import json
from collections import Counter
from html import escape
from pathlib import Path

from lxml import html

from benchmarks.provider_live import readable_text
from benchmarks.real50 import normalize


def verified_body(root, sha):
    body = gzip.decompress((root / 'bodies' / (sha + '.gz')).read_bytes())
    assert hashlib.sha256(body).hexdigest() == sha, 'Saved response hash mismatch'
    return body


def visible_text(body):
    if not body:
        return ''
    tree = html.fromstring(body)
    for node in tree.xpath('//script|//style|//template'):
        node.drop_tree()
    return ' '.join(tree.text_content().split())


def listing_audit(body, document):
    """Independent Lobsters fixture audit, not a universal completeness metric."""
    tree = html.fromstring(body)
    stories = tree.xpath('//main//li[@data-shortid]')
    text = normalize(document.get('text', ''))
    fields = []
    for story in stories:
        title = story.xpath('.//*[contains(concat(" ", normalize-space(@class), " "), " u-url ")]')
        comments = story.xpath('.//*[contains(concat(" ", normalize-space(@class), " "), " comments_label ")]//a')
        votes = story.xpath('.//*[contains(concat(" ", normalize-space(@class), " "), " voters ")]')
        field_values = [normalize(n[0].text_content()) if n else '' for n in (title, comments, votes)]
        fields.append({'id': story.get('data-shortid'), 'title': field_values[0],
                       'title_present': bool(field_values[0]) and field_values[0] in text,
                       'comments_present': bool(field_values[1]) and field_values[1] in text,
                       'votes_present': bool(field_values[2]) and field_values[2] in text})
    # Counts can repeat across stories, so title coverage is stronger evidence than vote coverage.
    covered = sum(f['title_present'] and f['comments_present'] and f['votes_present'] for f in fields)
    return {'version': 'lobsters-source-review-v1', 'stories': len(fields), 'covered': covered,
            'fields': fields, 'source_contains_point': 'point' in normalize(visible_text(body)),
            'usable_listing': document.get('outcome') == 'ok' and len(fields) >= 10
                              and covered == len(fields),
            'limit': 'Checks visible story titles, comment labels and vote tokens; does not certify every field or link.'}


def failure_stage(document, check):
    outcome = document.get('outcome')
    issues = document.get('metadata', {}).get('verification', {}).get('issues', [])
    if check.get('passed'):
        return 'passed'
    if outcome in ('robots_denied', 'robots_unavailable'):
        return 'robots_policy'
    if outcome == 'access_wall':
        return 'access_response'
    if 'target_identifier_changed' in issues:
        return 'target_changed'
    if outcome == 'error' and document.get('metadata', {}).get('transport_error'):
        return 'transport'
    if outcome == 'ok':
        return 'quality_review_required'
    return 'extraction_or_other'


def build(root):
    baseline = Path('reports/local-gap/final50')
    provider = Path('reports/provider-live50')
    comparison = json.loads((baseline / 'comparison.json').read_text())
    failures = [s for s in comparison['sites'] if s['after']['passes'] < s['after']['observations']]
    retest = root / 'retest'
    assert (retest / 'manifest.json').read_bytes() == (baseline / 'manifest.json').read_bytes()
    assert (retest / 'quality-contract.json').read_bytes() == (baseline / 'quality-contract.json').read_bytes()
    events = json.loads((retest / 'run-0/ours-events.json').read_text())
    observed_ids = {json.loads(p.read_text())['id'] for p in (retest / 'run-0').glob('ours-*.json')
                    if p.name != 'ours-events.json'}
    assert observed_ids == {s['id'] for s in failures}, 'Retest must contain exactly the failed sites'
    baseline_phase = json.loads((baseline / 'phase.json').read_text())
    retest_phase = json.loads((retest / 'phase.json').read_text())
    rows = []
    for site in failures:
        ident = site['id']
        row = json.loads((retest / f'run-0/ours-{ident}.json').read_text())
        doc = row['document']
        captures = [c for c in events['captures'] if c['parent'] == site['url']]
        bodies = [verified_body(retest, c['body_sha256']) for c in captures if c['body_sha256']]
        receipts = []
        for n in range(5):
            r = json.loads((provider / f'run-{n}/firecrawl-{ident}.json').read_text())
            sha = r['receipt']['response_sha256']
            raw = json.loads(verified_body(provider, sha))
            meta = (raw.get('data') or {}).get('metadata', {})
            receipts.append({'repetition': n, 'response_sha256': sha,
                'proxy_used': meta.get('proxyUsed'), 'final_url': meta.get('url'),
                'source_url': meta.get('sourceURL'), 'status': meta.get('statusCode'),
                'characters': len(readable_text(r['document']['text'])),
                'artifact': f'../provider-live50/run-{n}/firecrawl-{ident}.json'})
        item = {'id': ident, 'url': site['url'], 'stage': failure_stage(doc, row['readable_check']),
            'reason': doc['reason'], 'status': doc['status'], 'final_url': doc['final_url'],
            'fresh_passed': row['live_passed'], 'fresh_ms': row['elapsed_ms'],
            'baseline_passes': site['after']['passes'], 'baseline_observations': site['after']['observations'],
            'firecrawl_passes': site['firecrawl_historical']['quality_passes'], 'firecrawl_observations': 5,
            'firecrawl_receipts': receipts,
            'raw_evidence': [{'status': c['status'], 'url': c['url'], 'body_sha256': c['body_sha256'],
                             'role': c['role']} for c in captures],
            'body_excerpt': visible_text(bodies[-1])[:450] if bodies else '',
            'artifact': f'retest/run-0/ours-{ident}.json', 'review': ''}
        if ident == 'lobsters':
            item['listing_review'] = listing_audit(bodies[0], doc)
            item['review'] = 'Readable listing; frozen check expects the absent word point. Original score remains failed.'
        elif ident == 'britishmuseum':
            item['review'] = 'Firecrawl retrieved About content but also failed the frozen history keyword check. Retrieval advantage is real; strict-score win is not.'
        elif ident == 'ikea':
            item['review'] = 'Both return a category instead of product 00263850. Keep the target-identity rejection.'
        elif ident == 'nhk-ja':
            item['review'] = 'Firecrawl final URL is news.web.nhk/newsweb. Separate normal-policy destination probe succeeds; old URL remains unresolved.'
        else:
            item['review'] = 'Access response occurs before content parsing. No wall bypass attempted.'
        rows.append(item)
    report = {'scope': 'Only the 15 failures from the frozen 50-site development set',
        'fresh_original_url_attempts': len(rows), 'fresh_original_url_passes': sum(r['fresh_passed'] for r in rows),
        'historical_firecrawl_passes': sum(r['firecrawl_passes'] for r in rows),
        'historical_firecrawl_attempts': len(rows) * 5,
        'new_vendor_calls': retest_phase['provider_calls'],
        'runtime_changed': baseline_phase['runtime_sha256'] != retest_phase['runtime_sha256'],
        'stage_counts': dict(Counter(r['stage'] for r in rows)),
        'proxy_receipt_counts': dict(Counter(str(x['proxy_used']) for r in rows for x in r['firecrawl_receipts'])),
        'rows': rows,
        'limitations': ['One fresh original-URL repetition, historical Firecrawl evidence from five repetitions.',
            'Original-URL retest uses the same isolated robots override as the prior benchmark; production enforces robots.',
            'No newly recovered original URLs. NHK destination is a separate target, not a benchmark pass.',
            'Source-based Lobsters review does not modify historical or current frozen scores.',
            'Transport/backend/IP/geography effects were not independently isolated for Firecrawl.']}
    (root / 'failures.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    columns = ['id', 'stage', 'status', 'reason', 'fresh_ms', 'fresh_passed', 'firecrawl_passes', 'review', 'url']
    with (root / 'failures.csv').open('w') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    lines = ['# Focused failure evidence', '', report['scope'], '',
             '| Site | Local result | Fresh seconds | Firecrawl historical | Finding |',
             '| --- | --- | ---: | ---: | --- |']
    for r in rows:
        lines.append(f"| {r['id']} | {r['stage']} ({r['status']}) | {r['fresh_ms']/1000:.2f} | {r['firecrawl_passes']}/5 | {r['review']} |")
    lines += ['', *report['limitations']]
    (root / 'evidence.md').write_text('\n'.join(lines) + '\n')
    table = ''.join('<tr><td><a href="' + escape(r['artifact']) + '">' + escape(r['id']) +
        '</a></td><td>' + escape(r['stage']) + '</td><td>' + str(r['status']) +
        '</td><td>' + f"{r['fresh_ms']/1000:.2f}s" + '</td><td>' + str(r['firecrawl_passes']) +
        '/5</td><td>' + escape(r['review']) + '</td></tr>' for r in rows)
    (root / 'index.html').write_text('''<!doctype html><html lang="en"><meta charset="utf-8">
<title>Prism · Focused failure audit</title><style>
body{font:16px/1.5 system-ui;background:#f5f5f0;color:#1e2932;margin:40px auto;max-width:1250px;padding:0 24px}
h1{font-size:38px}table{border-collapse:collapse;background:white;width:100%}td,th{padding:13px;text-align:left;border-bottom:1px solid #ddd}th{background:#e1e7e5}a{color:#13585a}small{color:#57636d}code{background:#e1e7e5;padding:2px 5px}
</style><h1>What still fails, and why</h1>
<p>15 original failures tested once again. <b>0 new original-URL passes.</b> No vendor credits spent.</p>
<p><b>Practical NHK recovery</b> uses <a href="https://news.web.nhk/newsweb">the current destination</a> with normal robots enforcement. It is reported separately, because the old URL still times out.</p>
<p><b>Lobsters already returns a usable listing.</b> The frozen test expects a word absent from the page. The audit checks actual story titles, vote tokens and comment labels without altering the original score.</p>
<p>Firecrawl passed 59/75 historical observations on this subset. Its managed retrieval reaches content where our local requests receive access responses. The 75 receipts report 73 basic proxy responses, one stealth proxy response (USGS), and one error without a proxy label. They do not reveal which engine, IP or session behavior caused each success.</p>
<p><a href="analysis.md">Diagnosis and experiments</a> · <a href="failures.csv">CSV</a> · <a href="failures.json">Full evidence</a> · <a href="verification.json">Verification</a></p>
<table><thead><tr><th>Site</th><th>Failure stage</th><th>HTTP</th><th>Fresh timing</th><th>Firecrawl, earlier</th><th>Finding</th></tr></thead><tbody>''' + table + '''</tbody></table>
<p><small>The original-URL retest preserves the earlier isolated benchmark robots override. Production and the NHK destination probe enforce robots. All access walls remain intact. This is a focused development audit, not a new held-out benchmark or a simultaneous vendor rerun.</small></p></html>''')
    print(json.dumps({k:v for k,v in report.items() if k != 'rows'}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('reports/failure-focus'))
    build(parser.parse_args().output)
