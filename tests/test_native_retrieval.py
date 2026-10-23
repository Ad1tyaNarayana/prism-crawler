
import pytest
from aiohttp import web

from benchmarks.fixture import ARTICLE
from experiments.native_retrieval import NativeRetriever, PublicTunnel, extract_result
from optimal_crawler.models import CrawlConfig, Outcome
from tests.test_realworld_regressions import serve


async def test_native_renders_ordinary_javascript_with_honest_identity_and_no_cache(tmp_path):
    seen=[]
    async def handler(request):
        seen.append((request.path,request.headers.get('User-Agent')))
        if request.path=='/robots.txt':
            return web.Response(text='User-agent: *\nAllow: /\n')
        if request.path=='/script.js':
            return web.Response(text=f'document.querySelector("main").textContent={ARTICLE!r}',content_type='application/javascript')
        return web.Response(text='<title>Observatory</title><main>Loading...</main><script src="/script.js"></script>',content_type='text/html')
    async with serve(handler) as base:
        config=CrawlConfig(allow_private=True,min_delay=0,render_wait=.2,timeout=4,browser_timeout=10)
        async with NativeRetriever(config,{base}) as native:
            result=await native.fetch(base+'/page')
            doc=extract_result(base+'/page',result)
    assert doc['outcome']=='ok',doc
    assert ARTICLE in doc['text']
    assert any(path=='/script.js' for path,_ in seen)
    assert all(ua==config.user_agent for _,ua in seen)


@pytest.mark.parametrize('status',[200,403])
async def test_challenge_body_never_executes_scripts(status):
    seen=[]
    async def handler(request):
        seen.append(request.path)
        if request.path=='/robots.txt':
            return web.Response(text='User-agent: *\nAllow: /\n')
        return web.Response(status=status,content_type='text/html',text=
            '<title>Client Challenge</title><script>fetch("/challenge-solver")</script>')
    async with serve(handler) as base:
        async with NativeRetriever(CrawlConfig(allow_private=True,min_delay=0,timeout=3),{base}) as native:
            result=await native.fetch(base+'/page')
    assert result.outcome==Outcome.BLOCKED
    assert '/challenge-solver' not in seen


async def test_native_respects_robots_and_never_requests_denied_page():
    paths=[]
    async def handler(request):
        paths.append(request.path)
        return web.Response(text='User-agent: *\nDisallow: /page\n')
    async with serve(handler) as base:
        async with NativeRetriever(CrawlConfig(allow_private=True,min_delay=0,timeout=3),{base}) as native:
            result=await native.fetch(base+'/page')
    assert result.outcome==Outcome.ROBOTS
    assert paths==['/robots.txt']


async def test_native_rechecks_redirect_policy_before_following():
    paths=[]
    async def handler(request):
        paths.append(request.path)
        if request.path=='/robots.txt':
            return web.Response(text='User-agent: *\nDisallow: /denied\n')
        return web.Response(status=302,headers={'Location':'/denied'})
    async with serve(handler) as base:
        async with NativeRetriever(CrawlConfig(allow_private=True,min_delay=0,timeout=3),{base}) as native:
            result=await native.fetch(base+'/page')
    assert result.outcome==Outcome.ROBOTS
    assert '/denied' not in paths


async def test_tunnel_checks_scope_and_private_dns_before_opening_connection():
    tunnel=PublicTunnel({'https://127.0.0.1'})
    with pytest.raises(ValueError,match='Private'):
        await tunnel.addresses('127.0.0.1',443)
    with pytest.raises(ValueError,match='scope'):
        await tunnel.addresses('example.com',443)


async def test_native_stops_out_of_scope_javascript_and_post_requests():
    forbidden=[]
    async def other(request):
        forbidden.append(request.path)
        return web.Response(text='out of scope')
    async with serve(other) as extra:
        paths=[]
        async def handler(request):
            paths.append(request.path)
            if request.path=='/robots.txt':
                return web.Response(text='User-agent: *\nAllow: /\n')
            return web.Response(content_type='text/html',text=f'<main>{ARTICLE}</main>'
                f'<script>fetch("{extra}/forbidden"); fetch("/write",{{method:"POST"}})</script>')
        async with serve(handler,method='*') as base:
            async with NativeRetriever(CrawlConfig(allow_private=True,min_delay=0,render_wait=.2,timeout=3),{base}) as native:
                await native.fetch(base+'/page')
    assert not forbidden
    assert '/write' not in paths
