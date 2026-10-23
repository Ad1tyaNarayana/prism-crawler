"""Paired, normal-policy native Chromium experiment on the twelve access failures."""

import argparse
import asyncio
import gzip
import hashlib
import json
import shutil
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from benchmarks.access_diagnostics import response_evidence
from benchmarks.provider_live import quality_review, write_json
from benchmarks.real50 import SiteCase, runtime_digest
from experiments.native_retrieval import NativeRetriever, extract_result
from optimal_crawler.crawler import Crawler
from optimal_crawler.models import CrawlConfig, Document, Outcome
from optimal_crawler.network import Network
from optimal_crawler.urls import origin

EXPERIMENT = Path('experiments/native_retrieval.py')


async def run(args):
    root = args.output
    root.mkdir(parents=True, exist_ok=True)
    assert not (root/'phase.json').exists(), 'Use a fresh output directory'
    failures=json.loads(Path('reports/failure-focus/failures.json').read_text())['rows']
    selected={r['id'] for r in failures if r['stage']=='access_response'}
    assert len(selected)==12
    if args.case_ids:
        assert set(args.case_ids)<=selected
        selected=set(args.case_ids)
    cases=[SiteCase(**c) for c in json.loads(Path('benchmarks/real50.json').read_text()) if c['id'] in selected]
    digest=hashlib.sha256(EXPERIMENT.read_bytes()).hexdigest()
    phase={'started_at':datetime.now(UTC).isoformat(),'runtime_sha256':runtime_digest(),
        'experiment_sha256':digest,'cases':[c.id for c in cases], 'repetitions':args.repetitions,
        'robots_enforced':True,'wall_scripts_blocked':True,'vendor_calls':0,
        'page_deadline_seconds':45,'methods':['adaptive','native'],
        'limits':'Same crawler identity, parser and frozen topic/identity checks. Native is a prototype. One page at a time. Fresh contexts and databases. No external proxy.'}
    write_json(root/'phase.json',phase)
    shutil.copyfile(EXPERIMENT,root/'native_retrieval.py')
    shutil.copyfile(__file__,root/'harness.py')
    shutil.copyfile('benchmarks/real50.json',root/'manifest.json')
    shutil.copyfile('benchmarks/real50-review.json',root/'quality-contract.json')
    (root/'bodies').mkdir()
    records=[]
    for repetition in range(args.repetitions):
        for index,case in enumerate(cases):
            for method in (['adaptive','native'] if (index+repetition)%2==0 else ['native','adaptive']):
                assert hashlib.sha256(EXPERIMENT.read_bytes()).hexdigest()==digest
                assert runtime_digest()==phase['runtime_sha256']
                config=CrawlConfig(db=root/f'{method}-{case.id}-{repetition}.db',max_pages=1,
                    max_requests=150,workers=1,browser_concurrency=1,min_delay=.5,timeout=15,
                    browser_timeout=45,render_wait=2,retries=0,discover=False,follow_links=False,
                    decision_provider='none',run_timeout=60)
                started=time.perf_counter()
                captures=[]
                bodies={}
                events=[]
                tunnel=[]
                def capture(result,role,bodies=bodies,captures=captures):
                    sha=hashlib.sha256(result.body).hexdigest()
                    if result.body:
                        bodies[sha]=result.body
                    captures.append({'url':result.url,'role':role,'status':result.status,
                        'outcome':str(result.outcome),'reason':result.reason,
                        'body_sha256':sha if result.body else None,
                        'headers':response_evidence(result)['headers'],
                        'response_evidence':response_evidence(result)})
                try:
                    async with asyncio.timeout(48):
                        if method=='native':
                            async with NativeRetriever(config,{origin(case.url)}) as native:
                                fetched=await native.fetch(case.url)
                                document=extract_result(case.url,fetched)
                                events=native.events
                                for result,role in native.captures:
                                    capture(result,role)
                            tunnel=native.tunnel.events
                        else:
                            raw=Network._raw
                            async def observed(instance,url,headers,role,parent,asset=False,raw=raw,capture=capture,**kwargs):
                                result=await raw(instance,url,headers,role,parent,asset,**kwargs)
                                capture(result,role)
                                return result
                            with patch.object(Network,'_raw',observed):
                                async with Crawler(config) as crawler:
                                    await crawler.run([case.url])
                                    document=crawler.store.documents(crawler.run_id)[0]
                                    events=crawler.store.events('requests',crawler.run_id)
                except TimeoutError:
                    document=asdict(Document(case.url,case.url,Outcome.ERROR,method,reason='48-second experiment deadline'))
                elapsed=(time.perf_counter()-started)*1000
                row={'id':case.id,'url':case.url,'method':method,'repetition':repetition,
                    'elapsed_ms':elapsed,'document':document,'config':config.to_dict(),
                    'events':events,'tunnel':tunnel,'captures':captures,**quality_review(case,document)}
                records.append(row)
                for sha,body in bodies.items():
                    (root/'bodies'/(sha+'.gz')).write_bytes(gzip.compress(body,mtime=0))
                write_json(root/f'{method}-{case.id}-{repetition}.json',row)
                print(json.dumps({'id':case.id,'method':method,'repetition':repetition,
                    'passed':row['readable_check']['passed'],'outcome':document['outcome'],
                    'ms':round(elapsed),'reason':document['reason']}),flush=True)
    write_json(root/'finished.json',{'observations':len(records),'finished_at':datetime.now(UTC).isoformat(),
        'passes':{m:sum(r['readable_check']['passed'] for r in records if r['method']==m) for m in ['adaptive','native']}})


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--case-ids',nargs='+')
    parser.add_argument('--repetitions',type=int,default=1)
    asyncio.run(run(parser.parse_args()))
