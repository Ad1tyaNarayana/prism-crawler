"""Common benchmark-only access gate and receipts for native competitor networking."""

import asyncio
import gzip
import hashlib
import ipaddress
import json
import socket
import ssl
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import aiohttp
import certifi
from lxml import html
from protego import Protego

UA = "PrismComparison/0.1 (+local public-web research; robots enforced)"
BLOCKED_TYPES = {"image", "media", "font"}


def origin(url):
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


def cloudflare(status, headers, text=""):
    if headers.get("cf-mitigated", "").lower() == "challenge":
        return True
    lowered = text[:512000].lower()
    return (
        "<title>just a moment" in lowered
        and "/cdn-cgi/challenge-platform/" in lowered
        or status == 403
        and "cloudflare" in lowered
        and "sorry, you have been blocked" in lowered
    )


class Policy:
    def __init__(self, case, folder, private=False):
        self.case, self.folder, self.private = case, Path(folder), private
        self.origins = {
            origin(case["url"]),
            *case.get("asset_origins", []),
            *case.get("redirect_origins", []),
        }
        self.robots = {}
        self.locks = {}
        self.last = {}
        self.crawl_last = {}
        self.public_results = {}
        self.receipts = []
        self.denied = []
        self.cf = False
        self.session = None
        self.count = 0
        self.pages = self.launches = self.navigations = 0
        self.launch_ms = 0
        self.bodies = {}

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(
            headers={"User-Agent": UA},
            timeout=aiohttp.ClientTimeout(total=12),
            connector=aiohttp.TCPConnector(ssl=ssl.create_default_context(cafile=certifi.where())),
        )
        return self

    async def __aexit__(self, *_):
        await self.session.close()
        self.folder.mkdir(parents=True, exist_ok=True)
        for sha, body in self.bodies.items():
            (self.folder / (sha + ".gz")).write_bytes(gzip.compress(body, mtime=0))
        (self.folder / "policy.json").write_text(
            json.dumps(
                {
                    "requests": self.receipts,
                    "denied": self.denied,
                    "cloudflare": self.cf,
                    "request_count": self.count,
                    "browser_pages": self.pages,
                    "browser_launches": self.launches,
                    "browser_navigations": self.navigations,
                    "browser_launch_ms": self.launch_ms,
                },
                indent=2,
                default=str,
            )
        )

    async def public(self, url):
        p = urlsplit(url)
        if p.scheme not in {"https", "http"} or p.username or p.password or not p.hostname:
            return False
        if self.private:
            return True
        if p.hostname in self.public_results:
            return self.public_results[p.hostname]
        try:
            rows = await asyncio.get_running_loop().getaddrinfo(
                p.hostname, p.port or 443, type=socket.SOCK_STREAM
            )
            result = bool(rows) and all(ipaddress.ip_address(row[4][0]).is_global for row in rows)
            self.public_results[p.hostname] = result
            return result
        except (OSError, ValueError):
            return False

    def capture(self, url, status, headers, body, role):
        sha = hashlib.sha256(body).hexdigest() if body else None
        if sha:
            self.bodies[sha] = body
        h = {k.lower(): v for k, v in headers.items()}
        self.cf |= cloudflare(status, h, body.decode("utf-8", "replace"))
        self.receipts.append(
            {
                "url": url,
                "status": status,
                "role": role,
                "body_sha256": sha,
                "headers": {
                    k: v
                    for k, v in h.items()
                    if k in {"content-type", "location", "server", "cf-mitigated", "retry-after"}
                },
            }
        )

    async def policy_for(self, url):
        site = origin(url)
        lock = self.locks.setdefault(site, asyncio.Lock())
        async with lock:
            if site in self.robots:
                return self.robots[site]
            current = site + "/robots.txt"
            rule, reason = None, "robots_unavailable"
            for _ in range(5):
                if self.count >= 140 or not await self.public(current):
                    break
                self.count += 1
                try:
                    async with self.session.get(current, allow_redirects=False) as response:
                        body = await response.content.read(16_000_001)
                        self.capture(current, response.status, response.headers, body, "robots")
                        if self.cf:
                            reason = "cloudflare_skip"
                            break
                        if response.status in {301, 302, 303, 307, 308}:
                            target = urljoin(current, response.headers.get("location", ""))
                            if origin(target) not in self.origins:
                                reason = "robots_redirect_out_of_scope"
                                break
                            current = target
                            continue
                        if response.status in {404, 410}:
                            rule = Protego.parse("User-agent: *\nAllow: /\n")
                            reason = ""
                            break
                        decoded = body.decode("utf-8", "replace")
                        if (
                            response.status == 200
                            and len(body) <= 16_000_000
                            and "<html" not in decoded[:1000].lower()
                        ):
                            rule = Protego.parse(decoded)
                            reason = ""
                            break
                        break
                except (aiohttp.ClientError, TimeoutError):
                    break
            self.robots[site] = (rule, reason)
            return rule, reason

    async def allow(self, url, method="GET", role="document"):
        reason = ""
        if self.cf or "/cdn-cgi/challenge-platform/" in url or "challenges.cloudflare.com" in url:
            reason = "cloudflare_skip"
        elif method not in {"GET", "HEAD"}:
            reason = "method_denied"
        elif origin(url) not in self.origins:
            reason = "out_of_scope"
        elif not await self.public(url):
            reason = "dns_unavailable_or_private_destination"
        else:
            rules, reason = await self.policy_for(url)
            if rules is not None and not rules.can_fetch(url, UA):
                reason = "robots_denied"
            if not reason:
                site = origin(url)
                crawl_delay = float(rules.crawl_delay(UA) or 0)
                lane = (site, "document" if role == "document" else "asset")
                delay = 0.5 if role == "document" else 0.05
                now = time.monotonic()
                start = max(now, self.last.get(lane, now), self.crawl_last.get(site, now))
                self.last[lane] = start + delay
                self.crawl_last[site] = start + crawl_delay
                await asyncio.sleep(max(0, start - now))
                if self.count >= 140:
                    reason = "request_budget"
                else:
                    self.count += 1
        if reason:
            self.denied.append({"url": url, "method": method, "role": role, "reason": reason})
        return reason

    async def fetch(self, url, role="document"):
        visited = set()
        for _ in range(6):
            if url in visited:
                return {"url": url, "status": 0, "body": b"", "reason": "redirect_loop"}
            visited.add(url)
            reason = await self.allow(url, role=role)
            if reason:
                return {"url": url, "status": 0, "body": b"", "reason": reason}
            async with self.session.get(url, allow_redirects=False) as response:
                body = await response.content.read(16_000_001)
                self.capture(url, response.status, response.headers, body, role)
                if self.cf:
                    return {
                        "url": url,
                        "status": response.status,
                        "body": body,
                        "reason": "cloudflare_skip",
                    }
                if response.status in {301, 302, 303, 307, 308}:
                    url = urljoin(url, response.headers.get("location", ""))
                    continue
                return {
                    "url": url,
                    "status": response.status,
                    "body": body,
                    "reason": "" if 200 <= response.status < 300 else "http_error",
                }
        return {"url": url, "status": 0, "body": b"", "reason": "redirect_limit"}

    async def attach(self, context):
        async def route_handler(route):
            request = route.request
            if request.resource_type in BLOCKED_TYPES:
                await route.abort()
                return
            reason = await self.allow(
                request.url,
                request.method,
                "document" if request.is_navigation_request() else "asset",
            )
            if reason:
                await route.abort()
                return
            headers = {
                k: v
                for k, v in request.headers.items()
                if k.lower() not in {"cookie", "authorization"}
            }
            headers["user-agent"] = UA
            await route.continue_(headers=headers)

        await context.route("**/*", route_handler)
        if hasattr(context, "route_web_socket"):
            await context.route_web_socket("**/*", lambda ws: ws.close())

        async def observe(response):
            try:
                req = response.request
                body = await response.body() if req.is_navigation_request() else b""
                self.capture(
                    response.url,
                    response.status,
                    response.headers,
                    body,
                    "browser_document" if req.is_navigation_request() else "browser_asset",
                )
            except Exception:
                pass

        context.on("response", observe)


def observed_assets(raw, url):
    try:
        tree = html.fromstring(raw)
    except Exception:
        return []
    return sorted(
        {
            origin(urljoin(url, value))
            for value in tree.xpath('//script/@src|//link[@rel="stylesheet"]/@href')
            if urlsplit(urljoin(url, value)).scheme in {"http", "https"}
        }
        - {origin(url)}
    )
