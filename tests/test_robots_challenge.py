import pytest
from aiohttp import web

from optimal_crawler.models import CrawlConfig, Outcome
from optimal_crawler.network import Network
from optimal_crawler.store import Store
from tests.test_realworld_regressions import serve


@pytest.mark.parametrize('status,title',[(200,'Client Challenge'),(202,'JavaScript is disabled'),
                                         (200,'Site temporarily unavailable')])
async def test_success_status_html_is_not_an_empty_robots_policy(tmp_path,status,title):
    paths=[]
    async def handler(request):
        paths.append(request.path)
        return web.Response(status=status,content_type='text/html',
                            text=f'<!doctype html><html><title>{title}</title><body>Try later.</body></html>')
    async with serve(handler) as base:
        config=CrawlConfig(db=tmp_path/'robots.db',allow_private=True,min_delay=0,retries=0)
        store=Store(config.db)
        run=store.start(config,[base])
        try:
            async with Network(config,store,run,{base}) as network:
                result=await network.fetch(base+'/article')
        finally:
            store.close()
    assert result.outcome==Outcome.ROBOTS_UNAVAILABLE
    assert paths==['/robots.txt']
    assert 'HTML' in result.reason


@pytest.mark.parametrize('status',[200,202])
async def test_plain_robots_directives_in_success_response_are_enforced(tmp_path,status):
    paths=[]
    async def handler(request):
        paths.append(request.path)
        return web.Response(status=status,text='User-agent: *\nDisallow: /article\n',content_type='text/html')
    async with serve(handler) as base:
        config=CrawlConfig(db=tmp_path/'robots.db',allow_private=True,min_delay=0,retries=0)
        store=Store(config.db)
        run=store.start(config,[base])
        try:
            async with Network(config,store,run,{base}) as network:
                result=await network.fetch(base+'/article')
        finally:
            store.close()
    assert result.outcome==Outcome.ROBOTS
    assert paths==['/robots.txt']
