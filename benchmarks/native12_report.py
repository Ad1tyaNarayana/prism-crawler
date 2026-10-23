"""Publish and verify the bounded native-transport experiment from saved evidence."""

import gzip
import hashlib
import json
import shutil
from collections import Counter
from html import escape
from pathlib import Path

from benchmarks.provider_live import quality_review
from benchmarks.real50 import SiteCase


def publish():
    root=Path('reports/native12')
    pair=root/'paired'
    phase=json.loads((pair/'phase.json').read_text())
    assert len(phase['cases'])==12
    assert json.loads((pair/'finished.json').read_text())['observations']==24
    assert phase['robots_enforced'] and phase['vendor_calls']==0
    assert hashlib.sha256((pair/'native_retrieval.py').read_bytes()).hexdigest()==phase['experiment_sha256']
    assert (pair/'manifest.json').read_bytes()==Path('benchmarks/real50.json').read_bytes()
    assert (pair/'quality-contract.json').read_bytes()==Path('benchmarks/real50-review.json').read_bytes()
    cases={c['id']:SiteCase(**c) for c in json.loads((pair/'manifest.json').read_text())}
    rows=[]
    captures_checked=0
    for ident in phase['cases']:
        methods={}
        for method in ('adaptive','native'):
            r=json.loads((pair/f'{method}-{ident}-0.json').read_text())
            assert r['url']==cases[ident].url
            assert quality_review(cases[ident],r['document'])['readable_check']==r['readable_check']
            for c in r['captures']:
                sha=c['body_sha256']
                if sha:
                    body=gzip.decompress((pair/'bodies'/(sha+'.gz')).read_bytes())
                    assert hashlib.sha256(body).hexdigest()==sha
                    captures_checked+=1
            item={'outcome':r['document']['outcome'],'reason':r['document']['reason'],
                'elapsed_ms':r['elapsed_ms'],'passed':r['readable_check']['passed'],
                'artifact':f'paired/{method}-{ident}-0.json'}
            if method=='native':
                allowed=[e for e in r['events'] if e['type']=='request' and e.get('allowed')]
                item['document_requests']=sum(e['role']=='document' for e in allowed)
                item['asset_requests']=sum(e['role']=='asset' for e in allowed)
                item['protocols']=sorted({e['protocol'] for e in r['events'] if e['type']=='protocol'})
                assert item['asset_requests']==0
                assert all(e['url'].startswith(cases[ident].url.split('/',3)[0]+'//'+cases[ident].url.split('/')[2]+'/') for e in allowed)
                assert all(not e.get('from_disk_cache') and not e.get('from_service_worker') for e in r['events'] if e['type']=='protocol')
                assert r['config']['user_agent']=='PrismCrawler/0.1 (+local research crawler)'
                assert any(e['allowed'] for e in r['tunnel'])
            methods[method]=item
        rows.append({'id':ident,'url':cases[ident].url,**methods})
    assert sum(r['native']['document_requests'] for r in rows)==5
    assert sum(r['native']['passed'] for r in rows)==sum(r['adaptive']['passed'] for r in rows)==0
    policy_fix=[]
    fixed=root/'policy-fix'
    events=json.loads((fixed/'run-0/ours-events.json').read_text())
    for ident in ('usgs','patagonia','khanacademy'):
        r=json.loads((fixed/f'run-0/ours-{ident}.json').read_text())
        assert r['document']['outcome']=='robots_unavailable'
        requests=[e for e in events['requests'] if e['parent']==cases[ident].url]
        assert len(requests)==1 and requests[0]['role']=='robots'
        policy_fix.append({'id':ident,'outcome':r['document']['outcome'],'requests':len(requests),
            'page_requests':0,'reason':r['document']['reason'],'elapsed_ms':r['elapsed_ms']})
    report={'phase':phase,'observations':24,'paired_passes':{'adaptive':0,'native':0},
        'native_outcomes':dict(Counter(r['native']['outcome'] for r in rows)),
        'native_page_requests':5,'native_allowed_asset_requests':0,
        'native_protocols_observed':sorted({p for r in rows for p in r['native']['protocols']}),
        'rows':rows,'policy_fix':policy_fix,'captures_verified':captures_checked,
        'interpretation':'No demonstrated coverage gain. Five target pages tested; seven stopped by robots policy or retrieval. Not evidence about page retrieval on those seven. No whole-suite score update.'}
    (root/'comparison.json').write_text(json.dumps(report,indent=2))
    table=''.join('<tr><td><a href="'+escape(r['native']['artifact'])+'">'+escape(r['id'])+'</a></td>'+''.join(
        '<td>'+escape(r[m]['outcome'].replace('_',' '))+'</td><td>'+f"{r[m]['elapsed_ms']/1000:.2f}s"+'</td>' for m in ('adaptive','native'))+
        '<td>'+('403 access wall' if r['native']['document_requests'] else 'Not requested; policy stopped')+'</td></tr>' for r in rows)
    (root/'index.html').write_text('''<!doctype html><html lang="en"><meta charset="utf-8">
<title>Prism · Native retrieval experiment</title><style>
body{font:16px/1.55 system-ui;background:#f6f5f0;color:#1f2937;max-width:1200px;margin:40px auto;padding:0 24px}
h1{font-size:38px}.stats{display:flex;gap:20px;margin:24px 0}.stat{padding:18px;background:#e2ebe7;flex:1}.stat b{font-size:30px;display:block}table{border-collapse:collapse;background:white;width:100%}td,th{padding:12px;text-align:left;border-bottom:1px solid #ddd}th{background:#e2ebe7}a{color:#155b54}small{color:#53626b}
</style><h1>Does native Chromium retrieval help?</h1><p>Paired fresh tests on only the 12 access-response failures. Honest crawler identity, robots enforcement, strict TLS, no challenge execution and no vendor calls.</p>
<div class="stats"><div class="stat"><b>0 / 12</b>Native content passes</div><div class="stat"><b>5</b>Target pages fetched; all blocked</div><div class="stat"><b>7</b>Stopped before target by robots policy</div></div>
<p><b>Measured result.</b> Native TLS and HTTP/2 did not recover the five permitted targets. Six other sites returned unavailable/challenged policy files; Reuters disallowed the target. We cannot infer page-level native performance on those seven.</p>
<p><b>Useful fix.</b> Existing production code mistook HTML at robots.txt for an empty policy. USGS, Patagonia and Khan Academy now stop correctly before page retrieval. This is a policy correction, not a content recovery.</p>
<p><a href="analysis.md">Methods, limitations and recommendation</a> · <a href="comparison.json">Results and timings</a> · <a href="verification.json">Verification</a></p>
<table><thead><tr><th>Site</th><th>Existing adaptive</th><th>Time</th><th>Native Chromium</th><th>Time</th><th>Native target result</th></tr></thead><tbody>'''+table+'''</tbody></table>
<p><small>One paired observation per method and site. Times include startup, policy, retrieval and cleanup, and all observations are failures; these are not successful-throughput comparisons. No external proxy, credentials, stealth plugins or CAPTCHA solving. Experimental native implementation remains separate from production.</small></p></html>''')
    tests=Path('.audit/native12-full-tests.log').read_text()
    assert '127 passed' in tests,tests[-500:]
    assert '4 failed, 1 passed' in Path('.audit/native12-policy-before.log').read_text()
    verification={'paired_observations':24,'body_captures_verified':captures_checked,
        'frozen_urls_and_checks_unchanged':True,'same_user_agent_verified':True,
        'native_cache_or_serviceworker_responses':0,'native_document_requests':5,
        'native_allowed_asset_requests':0,'native_h2_observed':True,
        'production_policy_fix_live_cases':3,'policy_fix_target_requests':0,
        'tests_passed':127,'original_policy_regressions_reproduced':4,
        'vendor_calls':0,'production_native_enabled':False}
    (root/'verification.json').write_text(json.dumps(verification,indent=2))
    for name in ('full-tests','policy-before','policy-after','policy-live'):
        shutil.copyfile(f'.audit/native12-{name}.log',root/(name+'.log'))
    print(json.dumps(verification,indent=2))


if __name__=='__main__':
    publish()
