"""Experimental Chromium transport. Not wired into the production crawler."""

import asyncio
import base64
import socket
import time
from urllib.parse import urlsplit

from lxml import etree, html
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright
from protego import Protego

from optimal_crawler.browser import DOM_SNAPSHOT
from optimal_crawler.extract import Extractor, access_wall
from optimal_crawler.models import CrawlConfig, FetchResult, Outcome
from optimal_crawler.urls import canonicalize, is_public_ip, origin
from optimal_crawler.verification import ContentVerifier


class PublicTunnel:
    """Forwards encrypted browser bytes; pins resolved public addresses without TLS interception."""

    def __init__(self, origins, *, allow_private=False, byte_limit=64_000_000):
        self.destinations = {(urlsplit(o).hostname, urlsplit(o).port or
                              (443 if urlsplit(o).scheme == 'https' else 80)) for o in origins}
        self.allow_private, self.byte_limit = allow_private, byte_limit
        self.events, self.tasks = [], set()
        self.server = None

    async def start(self):
        self.server = await asyncio.start_server(self.handle, '127.0.0.1', 0, limit=65536)
        return f'http://127.0.0.1:{self.server.sockets[0].getsockname()[1]}'

    async def close(self):
        self.server.close()
        await self.server.wait_closed()
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*list(self.tasks), return_exceptions=True)

    async def addresses(self, host, port):
        if (host, port) not in self.destinations:
            raise ValueError('Host/port outside explicit scope')
        records = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
        addresses = list(dict.fromkeys(r[4][0] for r in records))
        if not addresses or (not self.allow_private and any(not is_public_ip(a) for a in addresses)):
            raise ValueError('Private or non-global destination denied')
        return addresses

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        upstream = None
        pumps = []
        event = {'allowed': False}
        try:
            async with asyncio.timeout(95):
                head = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 5)
                first, *headers = head.decode('latin1').split('\r\n')
                method, target, version = first.split(' ', 2)
                parsed = urlsplit('https://' + target if method == 'CONNECT' else target)
                host, port = parsed.hostname, parsed.port or (443 if method == 'CONNECT' else 80)
                event.update(host=host, port=port)
                addresses = await self.addresses(host, port)
                if method != 'CONNECT' and (method not in {'GET','HEAD'} or parsed.scheme != 'http'):
                    raise ValueError('Unsupported proxy request')
                last = None
                for address in addresses:
                    try:
                        remote, upstream = await asyncio.wait_for(asyncio.open_connection(address, port), 5)
                        break
                    except (OSError, TimeoutError) as exc:
                        last = exc
                if upstream is None:
                    raise OSError(f'No resolved address connected: {last}')
                event['allowed'] = True
                if method == 'CONNECT':
                    writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
                    await writer.drain()
                else:
                    path = parsed.path or '/'
                    if parsed.query:
                        path += '?' + parsed.query
                    kept = [h for h in headers if h and not h.lower().startswith(
                        ('connection:', 'proxy-connection:', 'proxy-authorization:'))]
                    upstream.write((f'{method} {path} {version}\r\n' + '\r\n'.join(kept) +
                                    '\r\nConnection: close\r\n\r\n').encode('latin1'))
                    await upstream.drain()

                async def transfer(source, destination):
                    size = 0
                    while chunk := await source.read(65536):
                        size += len(chunk)
                        if size > self.byte_limit:
                            raise ValueError('Tunnel byte budget exceeded')
                        destination.write(chunk)
                        await destination.drain()

                pumps = [asyncio.create_task(transfer(reader, upstream)),
                         asyncio.create_task(transfer(remote, writer))]
                done, _ = await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
                for completed in done:
                    completed.result()
        except (OSError, ValueError, TimeoutError, asyncio.IncompleteReadError,
                asyncio.LimitOverrunError) as exc:
            event['error'] = str(exc)[:200]
            if not event['allowed']:
                writer.write(b'HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
        finally:
            for pump in pumps:
                pump.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)
            for stream in (upstream, writer):
                if stream:
                    stream.close()
                    try:
                        await stream.wait_closed()
                    except OSError:
                        pass
            self.events.append(event)
            self.tasks.discard(task)


class NativeRetriever:
    def __init__(self, config: CrawlConfig, origins: set[str], *, ignore_robots=False):
        self.config, self.origins = config, origins
        self.ignore_robots = ignore_robots
        self.tunnel = PublicTunnel(origins, allow_private=config.allow_private,
                                   byte_limit=config.max_bytes * 4)
        self.events, self.captures = [], []
        self.rules, self.next_at = {}, {}
        self.locks, self.policy_locks = {}, {}
        self.requests = 0
        self.context = None

    async def __aenter__(self):
        proxy = await self.tunnel.start()
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True,
            proxy={'server': proxy}, args=['--proxy-bypass-list=<-loopback>',
                '--dns-prefetch-disable', '--disable-quic', '--disable-background-networking',
                '--force-webrtc-ip-handling-policy=disable_non_proxied_udp'])
        self.context = await self.browser.new_context(user_agent=self.config.user_agent,
            service_workers='block', accept_downloads=False, ignore_https_errors=False,
            viewport={'width':1280,'height':900})
        return self

    async def __aexit__(self, *_):
        await self.context.close()
        await self.browser.close()
        await self.playwright.stop()
        await self.tunnel.close()

    async def policy(self, url):
        key = origin(url)
        if key not in self.origins:
            return Outcome.OUT_OF_SCOPE, 'Origin outside explicit experiment scope'
        if self.ignore_robots:
            return Outcome.OK, 'Robots disabled by explicit experiment option'
        async with self.policy_locks.setdefault(key, asyncio.Lock()):
            if key not in self.rules:
                result = await self.navigate(key + '/robots.txt', robots=True)
                rule, error = None, ''
                if result.outcome != Outcome.OK or result.status not in {200, 404, 410}:
                    error = f'Native robots unavailable: HTTP {result.status}'
                    if result.reason:
                        error += f'; {result.reason}'
                elif result.status == 200:
                    if '<html' in result.text[:1000].lower() or result.body.lstrip().startswith(b'<'):
                        error = 'Robots returned HTML instead of a policy file'
                    else:
                        rule = Protego.parse(result.text)
                self.rules[key] = rule, error
        rule, error = self.rules[key]
        if error:
            return Outcome.ROBOTS_UNAVAILABLE, error
        if rule and not rule.can_fetch(url, self.config.user_agent):
            return Outcome.ROBOTS, 'robots.txt disallows this URL'
        return Outcome.OK, ''

    async def fetch(self, url):
        start = time.perf_counter()
        try:
            async with asyncio.timeout(self.config.browser_timeout):
                outcome, reason = await self.policy(url)
                result = await self.navigate(url) if outcome == Outcome.OK else FetchResult(
                    url, outcome=outcome, reason=reason)
        except TimeoutError:
            result = FetchResult(url, outcome=Outcome.ERROR, reason='Native page deadline exceeded')
        result.elapsed_ms = (time.perf_counter() - start) * 1000
        result.metadata['transport'] = 'chromium-native-via-local-public-tunnel'
        return result

    async def navigate(self, url, *, robots=False):
        started = time.monotonic()
        diagnostic = {'type': 'navigation', 'url': url, 'robots': robots,
                      'phase': 'creating_page', 'response_status': None, 'phases': []}
        self.events.append(diagnostic)

        def mark(phase):
            diagnostic['phase'] = phase
            diagnostic['phases'].append({'phase': phase,
                                         'elapsed_ms': (time.monotonic() - started) * 1000})

        page = await self.context.new_page()
        session = await self.context.new_cdp_session(page)
        handlers, failures = set(), []
        final = None
        main_frame = (await session.send('Page.getFrameTree'))['frameTree']['frame']['id']
        if robots:
            await session.send('Emulation.setScriptExecutionDisabled', {'value':True})
        await session.send('Network.enable')
        await session.send('Network.setCacheDisabled', {'cacheDisabled':True})
        session.on('Network.responseReceived', lambda e: self.events.append({
            'type':'protocol', 'url':e['response']['url'], 'protocol':e['response']['protocol'],
            'from_disk_cache':e['response'].get('fromDiskCache',False),
            'from_service_worker':e['response'].get('fromServiceWorker',False)}))
        session.on('Network.loadingFailed', lambda e: self.events.append({
            'type': 'loading_failed', 'url': url, 'error': e['errorText'],
            'url_role': 'navigation_owner', 'request_id': e['requestId'],
            'resource_type': e.get('type'),
            'elapsed_ms': (time.monotonic() - started) * 1000}))

        async def dispatch(event):
            nonlocal final
            request, ident = event['request'], event['requestId']
            target = canonicalize(request['url'])
            primary = event['resourceType']=='Document' and event['frameId']==main_frame
            response = 'responseStatusCode' in event or 'responseErrorReason' in event
            event_log = {'type':'response' if response else 'request','url':target,
                         'role':'robots' if robots else ('document' if primary else 'asset')}
            self.events.append(event_log)

            async def deny(outcome, reason):
                nonlocal final
                event_log.update(allowed=False, reason=reason)
                if primary:
                    final = FetchResult(target or url, outcome=outcome, reason=reason)
                await session.send('Fetch.failRequest', {'requestId':ident,'errorReason':'Aborted'})

            if not response:
                if not target or origin(target) not in self.origins:
                    return await deny(Outcome.OUT_OF_SCOPE,'Origin outside explicit experiment scope')
                if request['method'] not in {'GET','HEAD'} or (not primary and
                        (robots or event['resourceType'] not in {'Script','Stylesheet','XHR','Fetch'})):
                    return await deny(Outcome.UNSUPPORTED,'Resource or method not needed for bounded read')
                if primary and target != url and robots:
                    return await deny(Outcome.ROBOTS_UNAVAILABLE,'Robots redirect requires separate review')
                if not robots:
                    outcome, reason = await self.policy(target)
                    if outcome != Outcome.OK:
                        return await deny(outcome,reason)
                key = origin(target)
                async with self.locks.setdefault(key,asyncio.Lock()):
                    rule = self.rules.get(key,(None,''))[0]
                    delay = max(self.config.min_delay,float(rule.crawl_delay(self.config.user_agent) or 0) if rule else 0)
                    await asyncio.sleep(max(0,self.next_at.get(key,0)-time.monotonic()))
                    if self.requests >= self.config.max_requests:
                        return await deny(Outcome.BUDGET,'Native request budget exhausted')
                    self.requests += 1
                    self.next_at[key] = time.monotonic()+delay
                event_log['allowed']=True
                if primary:
                    mark('awaiting_response_headers')
                return await session.send('Fetch.continueRequest',{'requestId':ident})
            status = event.get('responseStatusCode',0)
            headers = {h['name'].lower():h['value'] for h in event.get('responseHeaders',[])}
            event_log['status']=status
            if primary and 'responseStatusCode' in event:
                diagnostic['response_status'] = status
                mark('response_headers_received')
            if status in {301,302,303,307,308}:
                return await session.send('Fetch.continueResponse',{'requestId':ident})
            if event.get('responseErrorReason'):
                return await deny(Outcome.ERROR,event['responseErrorReason'])
            length = headers.get('content-length','')
            if length.isdigit() and int(length)>self.config.max_bytes:
                return await deny(Outcome.UNSUPPORTED,'Native response exceeds byte budget')
            if primary:
                mark('reading_response_body')
            body_info = await session.send('Fetch.getResponseBody',{'requestId':ident})
            body = base64.b64decode(body_info['body']) if body_info.get('base64Encoded') else body_info['body'].encode()
            if primary:
                mark('response_body_complete')
            result = FetchResult(target,status,body,headers)
            self.captures.append((result,event_log['role']))
            if len(body)>self.config.max_bytes:
                return await deny(Outcome.UNSUPPORTED,'Decoded native response exceeds byte budget')
            wall = status in {401,403,429,503}
            if body and ('html' in headers.get('content-type','') or body.lstrip().startswith(b'<')):
                try:
                    wall = wall or access_wall(html.fromstring(result.text))
                except (etree.ParserError,ValueError):
                    pass
            if wall:
                result.outcome,result.reason = Outcome.BLOCKED,f'Native access response HTTP {status}; scripts not executed'
                if primary:
                    final=result
                event_log.update(allowed=False,reason=result.reason)
                return await session.send('Fetch.failRequest',{'requestId':ident,'errorReason':'Aborted'})
            if primary:
                final=result
            event_log['allowed']=True
            await session.send('Fetch.continueResponse',{'requestId':ident})

        async def guarded(event):
            try:
                await dispatch(event)
            except (PlaywrightError,ValueError,KeyError) as exc:
                failures.append(str(exc)[:250])
                try:
                    await session.send('Fetch.failRequest',{'requestId':event['requestId'],'errorReason':'Aborted'})
                except PlaywrightError:
                    pass

        def on_paused(event):
            task=asyncio.create_task(guarded(event))
            handlers.add(task)
            task.add_done_callback(handlers.discard)

        session.on('Fetch.requestPaused',on_paused)
        await session.send('Fetch.enable',{'patterns':[{'urlPattern':'*','requestStage':'Request'},
                                                       {'urlPattern':'*','requestStage':'Response'}]})
        try:
            try:
                await page.goto(url,wait_until='domcontentloaded',timeout=self.config.timeout*1000)
            except PlaywrightError as exc:
                if final is None:
                    phase = diagnostic['phase'].replace('_', ' ')
                    final=FetchResult(url,outcome=Outcome.ERROR,
                        reason=f'Native navigation failed while {phase}: {str(exc).splitlines()[0]}')
            if final is not None and final.outcome==Outcome.OK and not robots:
                if not 200<=final.status<300:
                    final.outcome,final.reason=Outcome.ERROR,f'Native HTTP {final.status}'
                else:
                    await asyncio.sleep(self.config.render_wait)
                    dom=await page.evaluate(DOM_SNAPSHOT,True)
                    rendered=FetchResult(page.url,final.status,dom.encode(),{'content-type':'text/html; charset=utf-8'})
                    self.captures.append((rendered,'rendered_dom'))
                    extracted=Extractor().extract(rendered)
                    if extracted.wall:
                        final.outcome,final.reason=Outcome.BLOCKED,'Rendered access response'
                    else:
                        final=rendered
            if final is None:
                final=FetchResult(url,outcome=Outcome.ERROR,reason='No native document response')
            if failures:
                final.metadata['interception_errors']=failures
                if final.outcome==Outcome.OK:
                    final.outcome,final.reason=Outcome.ERROR,'Native interception failed closed'
            final.metadata['navigation'] = diagnostic
            return final
        finally:
            diagnostic['elapsed_ms'] = (time.monotonic() - started) * 1000
            diagnostic['interception_errors'] = failures
            await page.close()
            for task in list(handlers):
                task.cancel()
            await asyncio.gather(*list(handlers),return_exceptions=True)


def extract_result(url,result):
    extractor,verifier=Extractor(),ContentVerifier()
    extraction=extractor.extract(result)
    verifier.apply(result,extraction,url)
    best=extraction.best
    outcome=result.outcome
    if outcome==Outcome.OK:
        if extraction.wall:
            outcome=Outcome.BLOCKED
        elif not best or best.quality<.5:
            outcome=Outcome.LOW_QUALITY
        elif best.verification and not best.verification.accepted:
            outcome=Outcome.INCOMPLETE
    return {'url':url,'final_url':result.url,'outcome':str(outcome),'status':result.status,
            'method':'native-browser','text':best.text if best and outcome==Outcome.OK else '',
            'title':best.title if best else '', 'parser':best.parser if best else '',
            'elapsed_ms':result.elapsed_ms,'reason':result.reason or extraction.reason,
            'metadata':{**result.metadata,'verification':verifier.metadata(best.verification)
                        if best and best.verification else None}}
