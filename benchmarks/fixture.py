import asyncio
import json
import time
from collections import Counter
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from aiohttp import web

ARTICLE = (
    "The observatory measured violet comets above the alpine ridge. "
    "Researchers recorded forty seven spectra using a solar powered telescope. "
    "The expedition publishes every observation so other laboratories can reproduce the analysis. "
    "Their instruments remained stable through the winter storm and recovered a rare helium signal."
)
SECOND = (
    "The coastal archive preserves handwritten navigation charts from the copper island expedition. "
    "Archivists restored thirty six maps using reversible conservation methods. "
    "Every scanned image includes its provenance and a public catalogue reference."
)
SHELL = "<div id='app'>Loading...</div>"


def html(body: str, title: str = "Observatory field notes", scripts: str = "") -> str:
    return f"""<!doctype html><html><head><title>{title}</title></head><body>
    <nav>BOILERPLATE_SENTINEL Home Login Privacy Subscribe Shopping Categories</nav>
    {body}<footer>BOILERPLATE_SENTINEL All rights reserved Newsletter Account</footer>
    {scripts}</body></html>"""


CASES = {
    "/static": {"markers": ["violet comets", "forty seven spectra", "rare helium signal"]},
    "/malformed": {"markers": ["coastal archive", "thirty six maps"]},
    "/short": {"markers": ["opens at 09:30", "Admission is free"]},
    "/table": {"markers": ["Helium", "Neon", "4.25", "8.60"]},
    "/jsonld": {"markers": ["violet comets", "forty seven spectra"]},
    "/hydration": {"markers": ["coastal archive", "thirty six maps"]},
    "/api-shell": {"markers": ["violet comets", "rare helium signal"]},
    "/js": {"markers": ["coastal archive", "thirty six maps"]},
    "/delayed": {"markers": ["violet comets", "rare helium signal"]},
    "/feed": {"markers": ["coastal archive", "thirty six maps"]},
    "/json": {"markers": ["violet comets", "rare helium signal"]},
    "/duplicate": {"markers": ["violet comets", "rare helium signal"]},
    "/near-duplicate": {"markers": ["violet comets", "rare helium signal"]},
    "/redirect": {"markers": ["violet comets", "rare helium signal"]},
    "/flaky": {"markers": ["coastal archive", "thirty six maps"]},
    "/private/secret": {"outcome": "robots_denied"},
    "/private/public": {"markers": ["coastal archive", "thirty six maps"]},
    "/wall": {"outcome": "access_wall"},
    "/huge": {"outcome": "unsupported"},
    "/redirect-private": {"outcome": "robots_denied"},
    "/article-about-captcha": {"markers": ["captcha research", "coastal archive"]},
    "/unicode": {"markers": ["वैज्ञानिक", "観測"]},
}


@dataclass
class Fixture:
    counts: Counter = field(default_factory=Counter)
    receipts: list[dict] = field(default_factory=list)
    version: int = 1
    base: str = ""

    def reset(self):
        self.counts.clear()
        self.receipts.clear()

    async def handle(self, request: web.Request) -> web.Response:
        path = request.path
        self.counts[path] += 1
        self.receipts.append({"path": path, "query": request.query_string,
                              "method": request.method, "time": time.time(),
                              "if_none_match": request.headers.get("If-None-Match"),
                              "ua": request.headers.get("User-Agent")})
        if path == "/robots.txt":
            return web.Response(text="User-agent: *\nDisallow: /private/*\nAllow: /private/public$\n"
                                f"Sitemap: {self.base}/sitemap.xml\n")
        if path == "/sitemap.xml":
            return web.Response(text=f"<sitemapindex><sitemap><loc>{self.base}/child.xml</loc>"
                                "</sitemap></sitemapindex>", content_type="application/xml")
        if path == "/child.xml":
            return web.Response(text="<urlset>" + "".join(
                f"<url><loc>{self.base}{p}</loc></url>" for p in CASES) + "</urlset>",
                                content_type="application/xml")
        if path in {"/redirect", "/redirect-private"}:
            raise web.HTTPFound("/static" if path == "/redirect" else "/private/secret")
        if path == "/redirect-nested":
            raise web.HTTPFound("/nested/index")
        if path == "/nested/render.js":
            return web.Response(text="document.querySelector('main').textContent=" + json.dumps(ARTICLE),
                                content_type="application/javascript")
        if path == "/redirect-loop":
            raise web.HTTPFound("/redirect-loop")
        if path == "/redirect-away":
            raise web.HTTPFound(request.query["to"])
        if path == "/slow":
            await asyncio.sleep(0.3)
            return web.Response(text=html(f"<article><p>{ARTICLE}</p></article>"), content_type="text/html")
        if path == "/slow-asset":
            await asyncio.sleep(1.5)
            return web.json_response({"analytics": "done"})
        if path == "/flaky" and self.counts[path] == 1:
            return web.Response(status=429, text="Slow down", headers={"Retry-After": "0.1"})
        if path == "/wall":
            return web.Response(text=html("<h1>Verify you are human</h1><p>Complete the CAPTCHA</p>",
                                         "Just a moment..."), status=403, content_type="text/html")
        if path == "/huge":
            return web.Response(body=b"x" * 300_000, content_type="text/html")
        if path == "/etag":
            etag = f'"v{self.version}"'
            if request.headers.get("If-None-Match") == etag:
                return web.Response(status=304, headers={"ETag": etag})
            return web.Response(text=html(f"<article><p>{ARTICLE} Revision {self.version}.</p></article>"),
                                headers={"ETag": etag}, content_type="text/html")
        if path in {"/json", "/data/article.json"}:
            return web.json_response({"title": "Observatory field notes", "articleBody": ARTICLE})
        if path == "/feed":
            return web.Response(text=f"<rss version='2.0'><channel><title>Archive updates</title>"
                                f"<item><title>Maps restored</title><link>{self.base}/malformed</link>"
                                f"<description>{SECOND}</description></item></channel></rss>",
                                content_type="application/rss+xml")
        if path == "/":
            body = "<h1>Research index</h1><p>Public field reports and instrument records.</p>" + \
                "".join(f"<a href='{p}'>{p}</a>" for p in CASES)
            body += "<a href='/static?utm_source=fixture#x'>same report</a>"
            return web.Response(text=html(body), content_type="text/html")
        if path == "/malformed":
            body = f"<main><h1>Navigation charts<p>{SECOND}<b>End of archive"
        elif path == "/short":
            body = "<main><h1>Opening hours</h1><p>The museum opens at 09:30. Admission is free.</p></main>"
        elif path == "/table":
            body = "<main><h1>Gas measurements</h1><p>Instrument readings in millimoles.</p>" \
                "<table><tr><th>Gas</th><th>Reading</th></tr><tr><td>Helium</td><td>4.25</td>" \
                "</tr><tr><td>Neon</td><td>8.60</td></tr></table></main>"
        elif path == "/jsonld":
            body = SHELL + "<script type='application/ld+json'>" + json.dumps({
                "@context": "https://schema.org", "@type": "NewsArticle",
                "headline": "Observatory field notes", "articleBody": ARTICLE}) + "</script>"
        elif path == "/hydration":
            body = SHELL + "<script id='__NEXT_DATA__' type='application/json'>" + json.dumps({
                "props": {"pageProps": {"article": {"title": "Navigation charts", "body": SECOND}}}}) \
                + "</script>"
        elif path == "/api-shell":
            body = "<link rel='alternate' type='application/json' href='/data/article.json'>" + SHELL
            body += "<script>fetch('/data/article.json').then(r=>r.json()).then(d=>{" \
                "document.querySelector('#app').innerHTML='<article><h1>'+d.title+'</h1><p>'+" \
                "d.articleBody+'</p></article>'})</script>"
        elif path in {"/js", "/delayed"} or path.startswith("/spa/"):
            text = ARTICLE if path == "/delayed" else SECOND
            delay = 600 if path == "/delayed" else 20
            body = SHELL + "<script>setTimeout(()=>{document.querySelector('#app').innerHTML=" \
                + json.dumps(f"<main><h1>Field report</h1><p>{text}</p></main>") + f"}},{delay})</script>"
        elif path == "/unicode":
            body = "<main><h1>अंतरिक्ष अध्ययन</h1><p>वैज्ञानिक नई दूरबीन से आकाश का अध्ययन करते हैं। " \
                "सभी परिणाम सार्वजनिक अभिलेख में उपलब्ध हैं।</p><p>研究者は山の観測所で星の光を記録した。" \
                "観測の結果は公開されている。</p></main>"
        elif path == "/hostile":
            body = f"<main><p>{ARTICLE}</p></main><script>" \
                "fetch('/private/secret');fetch('/mutate',{method:'POST',body:'no'});" \
                "fetch('http://169.254.169.254/latest/meta-data/');" \
                "new WebSocket('ws://127.0.0.1:1/');</script>"
        elif path == "/nested/index":
            body = "<main>Loading...</main><script src='render.js'></script>"
        elif path == "/slow-assets":
            body = f"<main><p>{ARTICLE}</p></main><script>fetch('/slow-asset')</script>"
        elif path == "/ambiguous":
            noise = "Subscribe for the latest research. Trending stories. Popular categories. " \
                "Sign in. Share on social media. Join our community. "
            body = f"<main><p>{noise * 7}</p></main><article><h1>Observatory field notes</h1><p>{ARTICLE}</p></article>"
        elif path == "/article-about-captcha":
            body = f"<article><h1>History of captcha research</h1><p>{SECOND}</p></article>"
        elif path in {"/private/secret", "/private/public", "/flaky"}:
            body = f"<main><h1>Navigation charts</h1><p>{SECOND}</p></main>"
        elif path in {"/static", "/duplicate", "/near-duplicate"}:
            text = ARTICLE + (" Updated today." if path == "/near-duplicate" else "")
            body = f"<article><h1>Observatory field notes</h1><p>{text}</p></article>"
        else:
            return web.Response(status=404, text="Not found")
        return web.Response(text=html(body), content_type="text/html")


@asynccontextmanager
async def site():
    fixture = Fixture()
    app = web.Application()
    app.router.add_route("*", "/{path:.*}", fixture.handle)
    runner = web.AppRunner(app)
    await runner.setup()
    server = web.TCPSite(runner, "127.0.0.1", 0)
    await server.start()
    port = server._server.sockets[0].getsockname()[1]
    fixture.base = f"http://127.0.0.1:{port}"
    try:
        yield fixture
    finally:
        await runner.cleanup()


async def main():
    async with site() as fixture:
        print(fixture.base, flush=True)
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
