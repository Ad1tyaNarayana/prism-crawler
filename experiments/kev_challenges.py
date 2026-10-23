"""Bounded, explicitly requested challenge interaction experiment. Not a crawler backend."""

import argparse
import asyncio
import hashlib
import json
import re
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import aiohttp
from playwright.async_api import Error as PlaywrightError

from benchmarks.access_diagnostics import response_evidence
from benchmarks.provider_live import quality_review
from benchmarks.real50 import SiteCase, runtime_digest
from experiments.native_retrieval import NativeRetriever, extract_result
from optimal_crawler.decisions import validate_answers
from optimal_crawler.models import CrawlConfig, FetchResult, Outcome
from optimal_crawler.urls import origin

IDS = ('britannica', 'smithsonian', 'britishmuseum', 'bookshop', 'apnews')
ENDPOINT = 'http://127.0.0.1:8019'
SELECTOR = 'button, input[type=checkbox], [role=checkbox], input[type=button], input[type=submit]'


def clean_url(url):
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, p.path, '', ''))


def save(path, data):
    path.write_text(json.dumps(data, indent=2, default=str))


async def observe(page):
    frames, controls, handles = [], [], {}
    for frame_index, frame in enumerate(page.frames):
        try:
            text = (await frame.locator('body').inner_text(timeout=1500))[:1800]
            frames.append({'frame': frame_index, 'url': clean_url(frame.url), 'text': text})
            for handle in (await frame.locator(SELECTOR).element_handles())[:30]:
                if not await handle.is_visible() or not await handle.is_enabled():
                    continue
                label = await handle.evaluate("""e => ({
                  text: (e.innerText || e.getAttribute('aria-label') ||
                    [...(e.labels || [])].map(x=>x.innerText).join(' ') || e.value || '').trim(),
                  type: e.getAttribute('role') || e.getAttribute('type') || e.tagName.toLowerCase(),
                  checked: e.checked === true || ['true','mixed'].includes(e.getAttribute('aria-checked'))
                })""")
                if label['checked']:
                    continue
                if not (label['type'] == 'checkbox' or re.search(
                        r'verify|human|retry|try again|continue', label['text'], re.I)):
                    continue
                ident = f'CLICK_{len(controls)}'
                controls.append({'id': ident, 'frame': frame_index,
                                 'label': label['text'][:180], 'type': label['type'],
                                 'enabled': True, 'checked': False})
                handles[ident] = ('dom', handle)
        except PlaywrightError as exc:
            frames.append({'frame': frame_index, 'url': clean_url(frame.url),
                           'observation_error': str(exc).splitlines()[0][:160]})
        ax_session = None
        try:
            ax_session = await page.context.new_cdp_session(page if frame == page.main_frame else frame)
            tree = await ax_session.send('Accessibility.getFullAXTree')
            for node in tree['nodes']:
                role = node.get('role', {}).get('value')
                name = node.get('name', {}).get('value', '')
                backend_id = node.get('backendDOMNodeId')
                if node.get('ignored') or role != 'checkbox' or not backend_id:
                    continue
                properties = {p['name']: p.get('value', {}).get('value')
                              for p in node.get('properties', [])}
                if properties.get('disabled') or properties.get('checked') in (True, 'true', 'mixed'):
                    continue
                if any(c['frame'] == frame_index and c['label'] == name for c in controls):
                    continue
                resolved = await ax_session.send('DOM.resolveNode', {'backendNodeId': backend_id})
                geometry = await ax_session.send('Runtime.callFunctionOn', {
                    'objectId': resolved['object']['objectId'], 'returnByValue': True,
                    'functionDeclaration': 'function(){const r=this.getBoundingClientRect(); return {x:r.x,y:r.y,w:r.width,h:r.height};}'})
                rect = geometry.get('result', {}).get('value', {})
                if rect.get('w', 0) <= 0 or rect.get('h', 0) <= 0:
                    continue
                offset = {'x': 0, 'y': 0}
                if frame != page.main_frame:
                    offset = await (await frame.frame_element()).bounding_box()
                    if not offset:
                        continue
                position = {'x': offset['x']+rect['x']+rect['w']/2,
                            'y': offset['y']+rect['y']+rect['h']/2}
                ident = f'CLICK_{len(controls)}'
                controls.append({'id': ident, 'frame': frame_index, 'label': name[:180],
                                 'type': 'checkbox', 'source': 'browser_accessibility',
                                 'position': position, 'enabled': True, 'checked': False})
                handles[ident] = ('ax', position)
        except PlaywrightError:
            pass  # Some same-process or detached frames have no independent CDP target.
        finally:
            if ax_session:
                await ax_session.detach()
    return {'title': await page.title(), 'url': clean_url(page.url),
            'frames': frames, 'controls': controls}, handles


def decision_payload(state):
    controls = [c for c in state['controls'] if c.get('enabled') is True
                and c.get('checked') is False]
    criteria = {c['id']: f"The visible {c['type']} labelled {c['label']!r} needs to be clicked."
                for c in controls}
    criteria.update({
        'WAIT': 'Verification is running; wait for it to finish.',
        'STOP': 'Verification failed or access is denied, with no available control.',
        'DONE': 'The requested article or website content is visible.',
    })
    questions = {'action': {'type': 'choice', 'instructions':
        'Which action matches this browser state?', 'criteria': criteria}}
    visible = '\n'.join(f"Frame {f['frame']} says: {f.get('text', '[unreadable]')}"
                        for f in state['frames'])
    control_text = '\n'.join(f"Visible control {c['id']}: {c['type']} labelled {c['label']}"
                             for c in controls) or 'No visible buttons or checkboxes.'
    model_state = control_text if controls else (
        f"Page title: {state['title']}\n{visible}\n{control_text}\n"
        f"Previous actions: {state.get('previous_actions', [])}")
    return {'model': 'kev-latest', 'state': model_state, 'questions': questions}


async def choose(state):
    payload = decision_payload(state)
    questions = payload['questions']
    start = time.perf_counter()
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15), trust_env=False) as session:
        async with session.post(ENDPOINT + '/v1/systemone', json=payload) as response:
            response.raise_for_status()
            result = await response.json()
    validate_answers(result, questions)
    return {'observation': state, 'input': payload, 'response': result,
            'elapsed_ms': (time.perf_counter()-start)*1000}


async def run_case(case, output, *, ignore_robots=False):
    directory = output / case.id
    directory.mkdir()
    config = CrawlConfig(max_pages=1, max_requests=100, timeout=12, browser_timeout=35,
                         retries=0, discover=False, follow_links=False, render_wait=2)
    record = {'id': case.id, 'url': case.url, 'started_at': datetime.now(UTC).isoformat(),
              'decisions': [], 'network': [], 'responses': [], 'clicks': 0,
              'config': config.to_dict(), 'error': '', 'recovered': False,
              'robots_mode': 'ignore' if ignore_robots else 'enforce'}
    start = time.perf_counter()
    async with NativeRetriever(config, {origin(case.url), 'https://challenges.cloudflare.com',
                                       'https://hagen.challenges.cloudflare.com'},
                               ignore_robots=ignore_robots) as native:
        record['browser_version'] = native.browser.version
        allowed, reason = await native.policy(case.url)
        record['policy'] = {'outcome': str(allowed), 'reason': reason}
        if allowed != Outcome.OK:
            record['error'] = 'Target robots policy prevented attempt'
            record['elapsed_ms'] = (time.perf_counter()-start)*1000
            record['policy_events'] = [{**event, 'url': clean_url(event['url'])}
                                       for event in native.events]
            save(directory/'result.json', record)
            return record
        context = await native.browser.new_context(user_agent=config.user_agent,
            service_workers='block', accept_downloads=False, ignore_https_errors=False,
            viewport={'width': 1280, 'height': 900},
            record_video_dir=str(directory), record_video_size={'width': 1280, 'height': 900})
        page = await context.new_page()
        page.set_default_timeout(2500)
        session = await context.new_cdp_session(page)
        await session.send('Network.enable')
        await session.send('Network.setCacheDisabled', {'cacheDisabled': True})
        tasks, errors = set(), []
        requests, last_document = 0, None
        locks, next_at = {}, {}

        async def route_request(route):
            nonlocal requests
            request = route.request
            target = request.url
            event = {'url': clean_url(target), 'method': request.method,
                     'resource': request.resource_type, 'allowed': False}
            record['network'].append(event)
            try:
                if requests >= 100 or origin(target) not in native.origins:
                    event['reason'] = 'Budget or explicit origin scope'
                    return await route.abort()
                is_challenge = '/cdn-cgi/challenge-platform/' in urlsplit(target).path
                if request.method not in {'GET', 'HEAD'} and not (request.method == 'POST' and is_challenge):
                    event['reason'] = 'Only reads and challenge-protocol POSTs permitted'
                    return await route.abort()
                policy, why = await native.policy(target)
                if policy != Outcome.OK:
                    event['reason'] = f'{policy}: {why}'
                    return await route.abort()
                key = origin(target)
                async with locks.setdefault(key, asyncio.Lock()):
                    rule = native.rules.get(key, (None, ''))[0]
                    delay = max(.05, float(rule.crawl_delay(config.user_agent) or 0) if rule else 0)
                    await asyncio.sleep(max(0, next_at.get(key, 0)-time.monotonic()))
                    next_at[key] = time.monotonic()+delay
                    requests += 1
                event['allowed'] = True
                await route.continue_()
            except (PlaywrightError, ValueError) as exc:
                event['reason'] = str(exc)[:180]
                errors.append(str(exc)[:180])
                try:
                    await route.abort()
                except PlaywrightError:
                    pass

        async def response_received(response):
            nonlocal last_document
            if response.request.resource_type != 'document':
                return
            if 300 <= response.status < 400:
                return
            headers = await response.all_headers()
            body = await response.body()
            fetched = FetchResult(response.url, response.status, body, headers)
            record['responses'].append({'url': clean_url(response.url), 'status': response.status,
                'body_sha256': hashlib.sha256(body).hexdigest(), 'evidence': response_evidence(fetched)})
            if response.frame == page.main_frame:
                last_document = fetched

        async def guarded_response(response):
            try:
                await response_received(response)
            except PlaywrightError as exc:
                errors.append(str(exc)[:180])

        def on_response(response):
            task = asyncio.create_task(guarded_response(response))
            tasks.add(task)
            task.add_done_callback(tasks.discard)

        await context.route('**/*', route_request)
        page.on('response', on_response)
        try:
            async with asyncio.timeout(60):
                try:
                    await page.goto(case.url, wait_until='domcontentloaded', timeout=18000)
                except PlaywrightError as exc:
                    record['navigation_warning'] = str(exc).splitlines()[0][:200]
                rule = native.rules.get(origin(case.url), (None, ''))[0]
                initial_wait = max(3, float(rule.crawl_delay(config.user_agent) or 0)+2 if rule else 3)
                record['initial_wait_seconds'] = initial_wait
                await asyncio.sleep(initial_wait)
                for turn in range(4):
                    state, handles = await observe(page)
                    state['turn'] = turn
                    state['previous_actions'] = [d['executed'] for d in record['decisions']]
                    screenshot = f'turn-{turn}.png'
                    await page.screenshot(path=str(directory/screenshot))
                    decision = await choose(state)
                    decision['screenshot'] = screenshot
                    action = decision['response']['answers']['action']['choice']
                    decision['selected'] = action
                    decision['executed'] = None
                    record['decisions'].append(decision)
                    if action in {'STOP', 'DONE'}:
                        decision['executed'] = action
                        break
                    if action == 'WAIT':
                        decision['executed'] = action
                        await asyncio.sleep(5)
                    elif action in handles and record['clicks'] < 2:
                        try:
                            selected = next(c for c in state['controls'] if c['id'] == action)
                            fresh, fresh_handles = await observe(page)
                            current = next((c for c in fresh['controls'] if all(
                                c[k] == selected[k] for k in ('frame', 'label', 'type'))), None)
                            if not current or fresh['url'] != state['url']:
                                decision['action_error'] = 'Selected control is no longer actionable'
                                break
                            decision['revalidated_control'] = current
                            kind, target = fresh_handles[current['id']]
                            if kind == 'dom':
                                await target.click(timeout=2500)
                            else:
                                await page.mouse.click(**target)
                            record['clicks'] += 1
                            decision['executed'] = action
                            await asyncio.sleep(.3)
                            decision['post_click_screenshot'] = f'post-click-{turn}.png'
                            await page.screenshot(path=str(directory/decision['post_click_screenshot']))
                            await asyncio.sleep(3)
                            decision['post_click_observation'], _ = await observe(page)
                        except PlaywrightError as exc:
                            decision['action_error'] = str(exc).splitlines()[0][:180]
                            break
                    else:
                        decision['action_error'] = 'Action unavailable or two-click budget reached'
                        break
                record['final_state'], _ = await observe(page)
                await page.screenshot(path=str(directory/'final.png'))
                await asyncio.gather(*list(tasks), return_exceptions=True)
                body = (await page.content()).encode()
                fetched = FetchResult(page.url, last_document.status if last_document else 0,
                    body, {'content-type': 'text/html; charset=utf-8'})
                if fetched.status in {401, 403, 429}:
                    fetched.outcome, fetched.reason = Outcome.BLOCKED, f'Final main document HTTP {fetched.status}'
                record['document'] = extract_result(case.url, fetched)
                record['quality'] = quality_review(case, record['document'])
                record['recovered'] = record['quality']['readable_check']['passed']
        except (TimeoutError, PlaywrightError, aiohttp.ClientError, ValueError) as exc:
            record['error'] = f'{type(exc).__name__}: {str(exc)[:250]}'
        finally:
            record['interception_errors'] = errors
            record['policy_captures'] = [{'url': clean_url(r.url), 'status': r.status,
                'outcome': str(r.outcome), 'reason': r.reason, 'role': role,
                'evidence': response_evidence(r), 'metadata': r.metadata}
                for r, role in native.captures]
            await context.unroute_all(behavior='ignoreErrors')
            await context.close()
            for event in record['network']:
                if not event['allowed'] and not event.get('reason'):
                    event['reason'] = 'Not dispatched before attempt ended (pacing or shutdown)'
            for task in list(tasks):
                task.cancel()
            await asyncio.gather(*list(tasks), return_exceptions=True)
            video = await page.video.path()
            destination = directory/'recording.webm'
            Path(video).rename(destination)
            record['video'] = destination.name
    record['elapsed_ms'] = (time.perf_counter()-start)*1000
    record['tunnel'] = native.tunnel.events
    record['policy_events'] = [{**event, 'url': clean_url(event['url'])}
                               for event in native.events]
    save(directory/'result.json', record)
    print(json.dumps({'id': case.id, 'recovered': record['recovered'], 'clicks': record['clicks'],
        'actions': [d['executed'] for d in record['decisions']],
        'elapsed_ms': round(record['elapsed_ms']), 'error': record['error']}), flush=True)
    return record


async def main(args):
    output = args.output
    output.mkdir(parents=True, exist_ok=False)
    async with aiohttp.ClientSession(trust_env=False) as session:
        async with session.get(ENDPOINT+'/v1/models') as response:
            response.raise_for_status()
            models = await response.json()
    cases = [SiteCase(**c) for c in json.loads(Path('benchmarks/real50.json').read_text())
             if c['id'] in (args.case_ids or IDS)]
    phase = {'started_at': datetime.now(UTC).isoformat(), 'models': models,
             'robots_mode': 'ignore' if args.ignore_robots else 'enforce',
             'runtime_sha256': runtime_digest(), 'cases': [c.id for c in cases],
             'vendor_calls': 0, 'browser': 'Installed headless Chromium with honest crawler UA',
             'decision_version': 'actionable-control-input-v2',
             'limits': 'One fresh context per site; four decisions; two visible-control clicks; 60-second interaction deadline; no proxy rotation or imported cookies.',
             'authorization': 'User explicitly requested KEV interaction with these challenges; production wall guards remain unchanged.'}
    save(output/'phase.json', phase)
    shutil.copyfile(__file__, output/'harness.py')
    shutil.copyfile(Path(__file__).with_name('native_retrieval.py'), output/'native_retrieval.py')
    rows = []
    for case in cases:
        assert runtime_digest() == phase['runtime_sha256']
        rows.append(await run_case(case, output, ignore_robots=args.ignore_robots))
    save(output/'summary.json', {'observations': len(rows), 'recovered': sum(r['recovered'] for r in rows),
        'finished_at': datetime.now(UTC).isoformat(),
        'results': [{'id': r['id'], 'recovered': r['recovered'], 'clicks': r['clicks'],
                     'elapsed_ms': r['elapsed_ms'], 'error': r['error']} for r in rows]})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--case-ids', nargs='+', choices=IDS)
    parser.add_argument('--ignore-robots', action='store_true',
                        help='Explicit experiment override; recorded in every result. Default enforces robots.')
    asyncio.run(main(parser.parse_args()))
