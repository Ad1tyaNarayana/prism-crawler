import asyncio
import ipaddress
import ssl
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import aiohttp
import certifi
from lxml import etree, html
from protego import Protego

from .extract import access_wall
from .models import CrawlConfig, FetchResult, Outcome
from .store import Store
from .urls import canonicalize, is_public_ip, origin


class PublicResolver(aiohttp.ThreadedResolver):
    async def resolve(self, host, port=0, family=0):
        records = await super().resolve(host, port, family)
        if not records or any(not is_public_ip(record["host"]) for record in records):
            raise OSError("Private or non-global address denied")
        return records


@dataclass
class OriginState:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    document_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    asset_slots: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(4))
    robots_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    robots: Protego | None = None
    robots_loaded: bool = False
    robots_error: str = ""
    robots_outcome: Outcome = Outcome.ROBOTS_UNAVAILABLE
    robots_delay: float = 0
    sitemaps: list[str] = field(default_factory=list)
    delay: float = 0
    next_at: float = 0
    next_document_at: float = 0
    cooldown_until: float = 0
    asset_delay: float = 0.05


def retry_after(value: str) -> float:
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            dt = parsedate_to_datetime(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return max(0.0, (dt - datetime.now(UTC)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return 0


class Network:
    def __init__(self, config: CrawlConfig, store: Store, run_id: str, origins: set[str]):
        self.config, self.store, self.run_id = config, store, run_id
        self.origins = origins
        self.asset_origins = {origin(u) for u in config.asset_origins}
        self.redirect_origins = {origin(u) for u in config.redirect_origins}
        self.excluded = {origin(u) for u in config.excluded_origins}
        self.states: dict[str, OriginState] = {}
        self.request_count = len(store.events("requests", run_id))
        self.session: aiohttp.ClientSession | None = None

    async def __aenter__(self):
        resolver = aiohttp.ThreadedResolver() if self.config.allow_private else PublicResolver()
        self.session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(resolver=resolver, limit=self.config.workers * 8,
                                           ssl=ssl.create_default_context(cafile=certifi.where())),
            timeout=aiohttp.ClientTimeout(total=self.config.timeout),
            max_line_size=65536, max_field_size=65536,
            cookie_jar=aiohttp.DummyCookieJar(), trust_env=False,
            headers={"User-Agent": self.config.user_agent,
                     "Accept": "text/html,application/xhtml+xml,application/json,application/xml;q=0.9,*/*;q=0.5"},
        )
        return self

    async def __aexit__(self, *_):
        if self.session:
            await self.session.close()

    def state(self, url: str) -> OriginState:
        key = origin(url)
        if key not in self.states:
            self.states[key] = OriginState(delay=self.config.min_delay,
                asset_delay=self.config.asset_delay,
                asset_slots=asyncio.Semaphore(self.config.asset_concurrency))
        return self.states[key]

    def scope_error(self, url: str, asset: bool = False, *, redirect: bool = False) -> str:
        allowed = self.origins | (self.asset_origins if asset else set()) \
            | (self.redirect_origins if redirect else set())
        if origin(url) in self.excluded:
            return "Origin explicitly excluded by the operator"
        if origin(url) not in allowed:
            return "Origin is outside the explicit crawl scope"
        host = urlsplit(url).hostname
        if not self.config.allow_private:
            try:
                ipaddress.ip_address(host.split("%")[0])
            except ValueError:
                if host.lower() == "localhost" or host.lower().endswith(".localhost"):
                    return "Private-network target denied"
            else:
                if not is_public_ip(host):
                    return "Private-network target denied"
        return ""

    async def robots(self, url: str) -> OriginState:
        state = self.state(url)
        async with state.robots_lock:
            if state.robots_loaded:
                return state
            robots_url = origin(url) + "/robots.txt"
            result = None
            for _ in range(6):
                for attempt in range(self.config.retries + 1):
                    extra = {"redirect": True} if origin(robots_url) in self.redirect_origins else {}
                    result = await self._raw(robots_url, {}, "robots", url, asset=True, **extra)
                    if result.outcome != Outcome.ERROR and result.status not in {429, 500, 502, 503, 504}:
                        break
                    if attempt == self.config.retries:
                        break
                if result.status not in {301, 302, 303, 307, 308}:
                    break
                destination = canonicalize(result.headers.get("location", ""), robots_url)
                if not destination or self.scope_error(destination, asset=True, redirect=True):
                    state.robots_error = "Robots redirect left authorized origins"
                    break
                robots_url = destination
            if result is None or result.outcome != Outcome.OK:
                state.robots_error = result.reason if result else "Robots unavailable"
            elif result.status in {401, 403, 429} or result.status >= 500 or 300 <= result.status < 400:
                state.robots_error = f"Robots unavailable or restricted, HTTP {result.status}"
            elif 200 <= result.status < 300:
                policy = result.text[:512_000].lstrip("\ufeff \t\r\n")
                if policy.startswith("<"):
                    state.robots_error = "Robots returned HTML/XML instead of a robots.txt policy"
                else:
                    state.robots = Protego.parse(policy)
                    state.robots_delay = float(state.robots.crawl_delay(self.config.user_agent) or 0)
                    state.sitemaps = list(state.robots.sitemaps)
            state.robots_loaded = True
        return state

    async def allowed(self, url: str, asset: bool = False, *, redirect: bool = False) -> tuple[Outcome, str]:
        error = self.scope_error(url, asset, redirect=redirect)
        if error:
            return Outcome.OUT_OF_SCOPE, error
        state = await self.robots(url)
        if state.robots_error:
            if self.request_count >= self.config.max_requests:
                return Outcome.BUDGET, state.robots_error
            return state.robots_outcome, state.robots_error
        if state.robots and not state.robots.can_fetch(url, self.config.user_agent):
            return Outcome.ROBOTS, "robots.txt disallows this URL"
        return Outcome.OK, ""

    async def fetch(self, url: str, *, headers: dict | None = None, role: str = "page",
                    parent: str = "", asset: bool = False, method: str = "GET") -> FetchResult:
        if method not in {"GET", "HEAD"}:
            return FetchResult(url, outcome=Outcome.UNSUPPORTED, reason="Only GET and HEAD are allowed")
        clean = canonicalize(url)
        if not clean:
            return FetchResult(url, outcome=Outcome.OUT_OF_SCOPE, reason="Unsupported or invalid URL")
        current = clean
        redirected = False
        visited = {current}
        for _ in range(6):
            outcome, reason = await self.allowed(current, asset, redirect=redirected)
            if outcome != Outcome.OK:
                return FetchResult(current, outcome=outcome, reason=reason)
            for attempt in range(self.config.retries + 1):
                extra = {"redirect": True} if redirected and origin(current) in self.redirect_origins else {}
                result = await self._raw(current, headers or {}, role, parent or url, asset,
                                         method=method, **extra)
                if result.status in {429, 503} and 0 < len(result.body) <= 512_000:
                    try:
                        challenge = access_wall(html.fromstring(result.text))
                    except (etree.ParserError, ValueError):
                        challenge = False
                    if challenge:
                        result.outcome, result.reason = Outcome.BLOCKED, "Visible access challenge, HTTP " + str(result.status)
                        break
                if result.outcome != Outcome.ERROR and result.status not in {429, 500, 502, 503, 504}:
                    break
                if attempt == self.config.retries:
                    break
            if result.outcome != Outcome.OK:
                return result
            if result.status in {301, 302, 303, 307, 308}:
                destination = canonicalize(result.headers.get("location", ""), current)
                if not destination:
                    return FetchResult(current, outcome=Outcome.ERROR, reason="Invalid redirect")
                if destination in visited:
                    result.outcome, result.reason = Outcome.ERROR, "Redirect loop detected"
                    return result
                visited.add(destination)
                current = destination
                redirected = True
                headers = {}
                continue
            if result.status in {401, 403}:
                result.outcome, result.reason = Outcome.BLOCKED, f"HTTP {result.status} access restriction"
            elif result.status == 304:
                result.outcome = Outcome.UNCHANGED
            elif not 200 <= result.status < 300:
                result.outcome, result.reason = Outcome.ERROR, f"HTTP {result.status}"
            return result
        return FetchResult(current, outcome=Outcome.ERROR, reason="Redirect limit exceeded")

    async def _raw(self, url: str, headers: dict, role: str, parent: str,
                   asset: bool = False, *, method: str = "GET", redirect: bool = False) -> FetchResult:
        error = self.scope_error(url, asset, redirect=redirect)
        if error:
            return FetchResult(url, outcome=Outcome.OUT_OF_SCOPE, reason=error)
        state = self.state(url)
        dependency = role == "browser_asset"
        queued_at = time.perf_counter()
        async with state.asset_slots if dependency else state.document_lock:
            async with state.lock:
                while True:
                    ready_at = max(state.next_at, state.cooldown_until,
                                   0 if dependency else state.next_document_at)
                    wait = ready_at - time.monotonic()
                    if wait <= 0:
                        break
                    await asyncio.sleep(wait)
                if self.request_count >= self.config.max_requests:
                    return FetchResult(url, outcome=Outcome.BUDGET, reason="Network request budget exhausted")
                self.request_count += 1
                state.next_at = time.monotonic() + max(state.robots_delay,
                    state.asset_delay if dependency else self.config.min_delay)
            queue_ms = (time.perf_counter() - queued_at) * 1000
            start = time.perf_counter()
            result = FetchResult(url)
            phase = "connection or response headers"
            try:
                async with self.session.request(method, url, headers=headers, allow_redirects=False) as response:
                    result.status = response.status
                    result.headers = dict(response.headers)
                    result.headers = {k.lower(): v for k, v in result.headers.items()}
                    phase = "response body"
                    if method != "HEAD" and response.content_length and response.content_length > self.config.max_bytes:
                        result.outcome, result.reason = Outcome.UNSUPPORTED, "Response exceeds byte limit"
                    else:
                        chunks, size = [], 0
                        async for chunk in response.content.iter_chunked(65536):
                            size += len(chunk)
                            if size > self.config.max_bytes:
                                result.outcome, result.reason = Outcome.UNSUPPORTED, "Decoded response exceeds byte limit"
                                break
                            chunks.append(chunk)
                        result.body = b"".join(chunks)
            except TimeoutError:
                result.outcome = Outcome.ERROR
                result.reason = f"HTTP timeout waiting for {phase} (request budget {self.config.timeout:g}s)"
                result.metadata["transport_error"] = {"kind": "timeout", "phase": phase,
                                                      "timeout_seconds": self.config.timeout}
            except (aiohttp.ClientError, OSError) as exc:
                result.outcome, result.reason = Outcome.ERROR, f"{type(exc).__name__}: {str(exc)[:240]}"
                result.metadata["transport_error"] = {"kind": type(exc).__name__, "phase": phase,
                                                      "os_errno": getattr(exc, "errno", None)}
            except asyncio.CancelledError:
                result.outcome, result.reason = Outcome.CANCELLED, "Request cancelled"
                raise
            finally:
                result.elapsed_ms = (time.perf_counter() - start) * 1000
                bad = result.status == 429 or result.status >= 500 or result.outcome == Outcome.ERROR
                floor = self.config.asset_delay if dependency else self.config.min_delay
                target = max(floor, result.elapsed_ms / 1000 * (0.1 if dependency else 0.5))
                prior_delay = state.asset_delay if dependency else state.delay
                delay = prior_delay
                if result.outcome != Outcome.CANCELLED:
                    delay = min(self.config.max_delay, max(target, prior_delay * 2, 0.05)) if bad \
                        else min(self.config.max_delay, max(floor, (prior_delay + target) / 2))
                    if dependency:
                        state.asset_delay = delay
                    else:
                        state.delay = delay
                server_wait = retry_after(result.headers.get("retry-after", "0"))
                wait = max(delay, state.robots_delay, server_wait)
                if bad or server_wait:
                    state.cooldown_until = max(state.cooldown_until, time.monotonic() + wait)
                if not dependency:
                    state.next_document_at = time.monotonic() + wait
                self.store.event("requests", self.run_id, {
                    "url": url, "parent": parent, "role": role, "method": method, "status": result.status,
                    "bytes": len(result.body), "elapsed_ms": result.elapsed_ms,
                    "next_delay_s": wait, "outcome": result.outcome, "reason": result.reason,
                    "queue_ms": queue_ms, "lane": "asset" if dependency else "document",
                    **result.metadata,
                })
            return result
