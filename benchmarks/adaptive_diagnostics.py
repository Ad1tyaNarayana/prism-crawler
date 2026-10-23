"""Guarded public-origin transport diagnostics; never follows a curl redirect."""

import asyncio
import hashlib
import json
import socket
from pathlib import Path
from urllib.parse import urlsplit

from optimal_crawler.models import CrawlConfig, Outcome
from optimal_crawler.network import Network
from optimal_crawler.store import Store
from optimal_crawler.urls import is_public_ip, origin


async def main():
    output = Path("reports/adaptive/diagnostics")
    output.mkdir(parents=True, exist_ok=False)
    urls = [
        "https://www3.nhk.or.jp/news/",
        "https://www.nature.com/nature/journal-information",
        "https://idp.nature.com/authorize?response_type=cookie&client_id=grover&redirect_uri=https%3A%2F%2Fwww.nature.com%2Fnature%2Fjournal-information",
    ]
    config = CrawlConfig(db=output / "checks.db", timeout=15, retries=0, min_delay=0.5)
    store = Store(config.db)
    run_id = store.start(config, urls)
    results = []
    async with Network(config, store, run_id, {origin(u) for u in urls}) as network:
        for index, url in enumerate(urls):
            allowed, reason = await network.allowed(url)
            row = {"url": url, "policy_outcome": str(allowed), "reason": reason}
            if allowed == Outcome.OK:
                if "idp.nature" in url:
                    result = await network.fetch(url, role="diagnostic")
                    row.update(
                        status=result.status,
                        outcome=str(result.outcome),
                        location=result.headers.get("location"),
                        sets_cookie="set-cookie" in result.headers,
                        body_bytes=len(result.body),
                    )
                else:
                    host = urlsplit(url).hostname
                    addresses = await asyncio.get_running_loop().getaddrinfo(
                        host, 443, type=socket.SOCK_STREAM
                    )
                    assert all(is_public_ip(a[4][0]) for a in addresses)
                    address = next(a[4][0] for a in addresses if ":" not in a[4][0])
                    state = network.state(url)
                    await asyncio.sleep(max(0, state.next_at - asyncio.get_running_loop().time()))
                    process = await asyncio.create_subprocess_exec(
                        "curl",
                        "--http2",
                        "--proxy",
                        "",
                        "--resolve",
                        f"{host}:443:{address}",
                        "--max-time",
                        "20",
                        "--connect-timeout",
                        "10",
                        "--max-filesize",
                        "16000000",
                        "--silent",
                        "--show-error",
                        "--compressed",
                        "--user-agent",
                        config.user_agent,
                        "--dump-header",
                        str(output / f"{index}.headers"),
                        "--output",
                        str(output / f"{index}.body"),
                        "--write-out",
                        "%{json}",
                        url,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    stdout, stderr = await process.communicate()
                    row.update(
                        curl_code=process.returncode,
                        error=stderr.decode()[:200],
                        transfer=json.loads(stdout) if stdout else {},
                    )
                    body = output / f"{index}.body"
                    if body.exists():
                        row["body_sha256"] = hashlib.sha256(body.read_bytes()).hexdigest()
            results.append(row)
            print(json.dumps(row), flush=True)
    store.finish(run_id, "completed", {"diagnostic": True})
    store.close()
    (output / "results.json").write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
