"""Run an offline HTTP/JavaScript/robots example using the public library API."""

import argparse
import asyncio
import json
from pathlib import Path

from aiohttp import web

from prism_crawler import AsyncCrawler, CrawlConfig, Outcome

PARAGRAPHS = (
    '<p>The field station publishes a complete daily research record. Each record includes '
    'the observation location, equipment calibration and the original measurement notes. '
    'These details must remain together when a downstream application retrieves the page.</p>'
    '<p>Researchers compare the measurements with earlier records before publishing their '
    'conclusions. The source archive retains revisions so that other teams can check the '
    'analysis and reproduce the reported observations using the same methods.</p>'
    '<p>The final source marker is PRISM-LOCAL-COMPLETE. This example verifies that the '
    'extracted document includes the end of the source, and that a robots exclusion prevents '
    'the forbidden endpoint from receiving a request.</p>'
)


async def main(output: Path) -> None:
    denied_requests = 0

    async def handler(request):
        nonlocal denied_requests
        if request.path == '/robots.txt':
            return web.Response(text='User-agent: *\nDisallow: /private\n')
        if request.path == '/private':
            denied_requests += 1
            return web.Response(status=500)
        if request.path == '/rendered':
            body = '<main>Loading...</main><script>setTimeout(()=>{' \
                   'document.querySelector("main").innerHTML=' + json.dumps(PARAGRAPHS) + '},40)</script>'
        else:
            body = '<main><h1>Field station notes</h1>' + PARAGRAPHS + '</main>' \
                   '<nav><a href="/rendered">Rendered record</a><a href="/private">Private</a></nav>'
        return web.Response(text='<html><title>Field station</title>'+body+'</html>',
                            content_type='text/html')

    app = web.Application()
    app.router.add_get('/{tail:.*}', handler)
    runner = web.AppRunner(app)
    await runner.setup()
    server = web.TCPSite(runner, '127.0.0.1', 0)
    await server.start()
    try:
        base = f'http://127.0.0.1:{server._server.sockets[0].getsockname()[1]}'
        config = CrawlConfig(db=output.with_suffix('.db'), allow_private=True, max_pages=10,
                             discover=False, min_delay=0, render_wait=.1, scroll_steps=0)
        async with AsyncCrawler(config) as crawler:
            result = await crawler.crawl(base+'/')
        assert len(result.successful) == 2, result.summary
        assert len(result.failures) == 1 and result.failures[0].outcome is Outcome.ROBOTS
        assert all('PRISM-LOCAL-COMPLETE' in document.text for document in result.successful)
        assert result.summary['browser_pages'] == 1
        assert denied_requests == 0
        result.to_jsonl(output)
        print(json.dumps({'summary': result.summary, 'export': str(output),
                          'denied_endpoint_requests': denied_requests}, indent=2))
    finally:
        await runner.cleanup()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('data/local-example.jsonl'))
    asyncio.run(main(parser.parse_args().output))
