"""Record a short, real local crawl and crash-recovery demo. No external requests."""
import argparse
import asyncio
import json
import shutil
import sys
from pathlib import Path

from aiohttp import web
from playwright.async_api import async_playwright


async def run(output: Path):
    output.mkdir(parents=True, exist_ok=False)
    state = {'stage': 'Ready', 'rows': [], 'detail': 'A real local run, recorded as it happens.'}
    markup = Path(__file__).with_suffix('.html').read_text()

    async def page(request):
        return web.Response(text=markup, content_type='text/html')

    async def status(request):
        return web.json_response(state)

    app = web.Application()
    app.router.add_get('/', page)
    app.router.add_get('/status', status)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context(viewport={'width': 1440, 'height': 900},
                                                record_video_dir=str(output / 'video'),
                                                record_video_size={'width': 1440, 'height': 900})
            tab = await context.new_page()
            await tab.goto(f'http://127.0.0.1:{port}/')
            await asyncio.sleep(5)
            state.update(stage='Running', detail='HTTP first. Render JavaScript only when needed. Retry temporary errors.')
            command = [sys.executable, '-m', 'benchmarks.load_recovery', 'suite',
                       '--output', str(output / 'run'), '--origins', '2',
                       '--pages-per-origin', '80', '--workers', '2', '--fixture-delay', '.06',
                       '--mixed', '--recovery']
            with (output / 'stderr.log').open('w') as errors:
                proc = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE,
                                                            stderr=errors)
                with (output / 'stdout.jsonl').open('w') as log:
                    async for line in proc.stdout:
                        log.write(line.decode())
                        log.flush()
                        row = json.loads(line)
                        state['rows'].append(row)
                        state['detail'] = 'Next: kill the crawler, reopen SQLite, and recover unfinished work.'
                    await proc.wait()
            results = json.loads((output / 'run' / 'summary.json').read_text())
            valid = proc.returncode == 0 and len(results) == 3 and all(r['correct'] for r in results)
            state.update(stage='Verified' if valid else 'Failed', results=results,
                         detail='Exact URL sets and first/last content markers checked. Zero requests to robots-denied pages.')
            (output / 'status.json').write_text(json.dumps(state, indent=2))
            await asyncio.sleep(10)
            await tab.screenshot(path=str(output / 'demo.png'))
            video = tab.video
            await context.close()
            await video.save_as(str(output / 'demo.webm'))
            await browser.close()
            shutil.rmtree(output / 'video')
            (output / 'index.html').write_text(markup.replace("fetch('/status')", "fetch('status.json')"))
            if not valid:
                raise RuntimeError('Demo experiment did not pass; inspect the recorded evidence')
            print(json.dumps({'video': str(output / 'demo.webm'), 'correct': valid}))
    finally:
        await runner.cleanup()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(run(parser.parse_args().output))
