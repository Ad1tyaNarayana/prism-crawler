"""Local source-backed examples for deferred content and document formats."""
import io
from collections import Counter
from contextlib import asynccontextmanager

from aiohttp import web
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from benchmarks.fixture import ARTICLE, SECOND


def pdf_bytes(*, pages=1, blank=False, locked=False):
    writer = PdfWriter()
    for number in range(pages):
        page = writer.add_blank_page(width=612, height=792)
        if not blank:
            font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                                     NameObject('/Subtype'): NameObject('/Type1'),
                                     NameObject('/BaseFont'): NameObject('/Helvetica')})
            page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):
                DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
            stream = DecodedStreamObject()
            text = f'Public observatory report page {number + 1}. ' + ARTICLE
            stream.set_data(('BT /F1 10 Tf 40 740 Td (' + text + ') Tj ET').encode())
            page[NameObject('/Contents')] = writer._add_object(stream)
    writer.add_metadata({'/Title': 'Public observatory report'})
    if locked:
        writer.encrypt('fixture-password', algorithm='RC4-128')
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


@asynccontextmanager
async def site():
    counts = Counter()
    papers = {'/paper.pdf': pdf_bytes(), '/scan.pdf': pdf_bytes(blank=True),
              '/locked.pdf': pdf_bytes(locked=True), '/long.pdf': pdf_bytes(pages=101),
              '/malformed.pdf': b'%PDF-1.7\nnot a valid document'}

    async def handler(request):
        counts[request.path] += 1
        if request.path == '/robots.txt':
            return web.Response(text='User-agent: *\nDisallow: /private/\n')
        if request.path in papers:
            return web.Response(body=papers[request.path], content_type='application/pdf')
        if request.path == '/wall':
            return web.Response(text='Access denied', status=403)
        if request.path == '/chunk':
            return web.json_response({'text': SECOND})
        if request.path == '/deferred':
            script = """let busy=false; window.addEventListener('scroll', async () => {
                if (busy || !document.querySelector('#more')) return; busy=true;
                let data=await (await fetch('/chunk')).json();
                const p=document.createElement('p'); p.textContent=data.text;
                document.querySelector('main').append(p); document.querySelector('#more').remove();
            });"""
        elif request.path == '/infinite':
            script = """window.addEventListener('scroll', () => {
                const p=document.createElement('p'); p.style.height='1200px';
                p.textContent='Additional public catalogue record with provenance and a new instrument history.';
                document.querySelector('main').append(p);
            });"""
        else:
            return web.Response(text=f'<main><h1>Observatory</h1><p>{ARTICLE}</p></main>', content_type='text/html')
        markup = f'<main><h1>Observatory</h1><p>{ARTICLE}</p><div style="height:1400px"></div>' \
            '<div id="more" aria-busy="true">Loading more entries...</div></main>' \
            f'<script>{script}</script>'
        return web.Response(text=markup, content_type='text/html')

    app = web.Application()
    app.router.add_get('/{path:.*}', handler)
    runner = web.AppRunner(app)
    await runner.setup()
    server = web.TCPSite(runner, '127.0.0.1', 0)
    await server.start()
    port = server._server.sockets[0].getsockname()[1]
    try:
        yield f'http://127.0.0.1:{port}', counts
    finally:
        await runner.cleanup()
