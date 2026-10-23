"""Drive the installed CLI against deterministic local pages and export its results."""
import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

from aiohttp import web

from benchmarks.fixture import ARTICLE, SECOND


async def run(output: Path):
    output.mkdir(parents=True, exist_ok=True)
    db = output / "demo.db"
    if db.exists():
        raise ValueError("Use a fresh output directory to preserve previous evidence")
    receipts = Counter()
    sections = f"<h1>Observatory report</h1><h2>New observations</h2><p>{ARTICLE}</p>" \
        f"<h2>Historical archive</h2><p>{SECOND}</p>"
    description = "A public observatory shares information about its research, historical collections " \
        "and scientific instruments. Explore this page to learn about its programmes and new observations."

    async def handler(request):
        receipts[request.path] += 1
        if request.path == "/robots.txt":
            return web.Response(text="User-agent: *\nDisallow: /private/\n")
        if request.path == "/products/instrument-123456":
            raise web.HTTPFound("/catalogue")
        if request.path == "/wall":
            return web.Response(text="Access denied", status=403)
        if request.path == "/shell":
            text = "<script type='application/ld+json'>" + json.dumps(
                {"@type": "WebPage", "description": description}) + "</script>" \
                "<main>Loading...</main><script>setTimeout(()=>document.querySelector('main').innerHTML=" \
                + json.dumps(sections) + ",650)</script>"
        else:
            text = "<main>" + sections + "</main>"
        return web.Response(text=text, content_type="text/html")

    app = web.Application()
    app.router.add_get("/{path:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    server = web.TCPSite(runner, "127.0.0.1", 0)
    await server.start()
    port = server._server.sockets[0].getsockname()[1]
    base = f"http://127.0.0.1:{port}"

    async def cli(*arguments):
        process = await asyncio.create_subprocess_exec(sys.executable, "-m", "optimal_crawler.cli", *arguments,
                                                      stdout=asyncio.subprocess.PIPE,
                                                      stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await process.communicate()
        assert process.returncode == 0, stderr.decode() + stdout.decode()
        return stdout.decode()

    try:
        seeds = [base + path for path in ["/static", "/shell", "/products/instrument-123456",
                                         "/private/secret", "/wall"]]
        stdout = await cli("crawl", *seeds, "--db", str(db), "--allow-private", "--no-discover",
                           "--no-follow-links", "--delay", "0", "--browser-timeout", "4", "--render-wait", "0.1")
        (output / "crawl.stdout.json").write_text(stdout)
        await cli("export", "--db", str(db), "--output", str(output / "pages.jsonl"))
        docs = [json.loads(line) for line in (output / "pages.jsonl").read_text().splitlines()]
        by_path = {d["url"].removeprefix(base): d for d in docs}
        assert by_path["/static"]["outcome"] == "ok" and by_path["/static"]["method"] == "http"
        assert by_path["/shell"]["outcome"] == "ok" and by_path["/shell"]["method"] == "browser"
        assert all("thirty six maps" in by_path[path]["text"] for path in ["/static", "/shell"])
        assert by_path["/products/instrument-123456"]["outcome"] == "incomplete"
        assert by_path["/private/secret"]["outcome"] == "robots_denied"
        assert by_path["/wall"]["outcome"] == "access_wall"
        assert receipts["/private/secret"] == 0
        result = {"kind": "local_cli_demonstration", "paid_services": False,
                  "outcomes": {path: d["outcome"] for path, d in by_path.items()},
                  "receipts": dict(receipts), "assertions_passed": True,
                  "limits": "Controlled local examples; not a live-site coverage benchmark."}
        (output / "evidence.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))
    finally:
        await runner.cleanup()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("reports/verification/cli"))
    asyncio.run(run(parser.parse_args().output))


if __name__ == "__main__":
    main()
