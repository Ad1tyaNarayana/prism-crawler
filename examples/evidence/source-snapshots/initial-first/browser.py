import asyncio
import base64
import time

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from .extract import Extractor
from .models import CrawlConfig, FetchResult, Outcome
from .network import Network
from .urls import canonicalize
from .verification import ContentVerifier

DOM_SNAPSHOT = """serialize => {
    if (!document.documentElement) return '';
    let count = 0;
    function copy(node, depth = 0) {
        if (++count > 50000 || depth > 100) throw new Error('DOM snapshot exceeds node or depth limit');
        const result = node.cloneNode(false);
        for (const child of node.childNodes) result.appendChild(copy(child, depth + 1));
        if (node.shadowRoot) {
            const shadow = document.createElement('div');
            shadow.setAttribute('data-prism-shadow-root', 'open');
            for (const child of node.shadowRoot.childNodes) shadow.appendChild(copy(child, depth + 1));
            result.appendChild(shadow);
        }
        return result;
    }
    const tree = copy(document.documentElement);
    if (serialize) return '<!DOCTYPE html>' + tree.outerHTML;
    const root = tree.querySelector('main,article,[role=main]') || tree.querySelector('body');
    if (!root) return '';
    root.querySelectorAll('nav,footer,header,script,style,noscript,aside,form').forEach(e => e.remove());
    return (root.textContent || '').trim();
}"""

SCROLL_STATE = """() => ({
    y: window.scrollY, height: document.documentElement.scrollHeight,
    viewport: window.innerHeight
})"""


async def finish_route(operation):
    task = asyncio.create_task(operation)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Playwright must acknowledge the terminal action before the handler ends.
        await task
        raise


class Browser:
    def __init__(self, config: CrawlConfig, network: Network):
        self.config, self.network = config, network
        self._playwright = None
        self._browser = None
        self._proxy = None
        self._proxy_tasks = set()
        self._launch_lock = asyncio.Lock()
        self._slots = asyncio.Semaphore(config.browser_concurrency)
        self.pages = 0
        self.launch_ms = 0.0

    async def _scroll(self, page, deadline: float, handlers: dict, wait_for_dependencies: bool) -> dict:
        steps, grew = 0, False
        state = await page.evaluate(SCROLL_STATE)
        while steps < self.config.scroll_steps and time.monotonic() < deadline:
            if state["y"] + state["viewport"] >= state["height"] - 2:
                break
            await page.evaluate("window.scrollBy(0, window.innerHeight * 0.9)")
            steps += 1
            previous = ""
            stable_since = time.monotonic()
            settle = max(0.3, min(self.config.render_wait, 1.0))
            while time.monotonic() < deadline:
                current = await page.evaluate(DOM_SNAPSHOT, False)
                if current != previous:
                    stable_since = time.monotonic()
                    previous = current
                if time.monotonic() - stable_since >= settle and (not wait_for_dependencies or not handlers):
                    break
                await asyncio.sleep(0.1)
            after = await page.evaluate(SCROLL_STATE)
            grew = after["height"] > state["height"] + 2
            state = after
        return {"steps": steps, **state,
                "dependency_wait": "all" if wait_for_dependencies else "content_settling",
                "bottom": state["y"] + state["viewport"] >= state["height"] - 2,
                "limit_reached": grew and (steps >= self.config.scroll_steps or time.monotonic() >= deadline)}

    async def _start(self):
        async with self._launch_lock:
            if self._browser is None:
                start = time.perf_counter()
                self._playwright = await async_playwright().start()
                self._proxy = await asyncio.start_server(self._deny_native, "127.0.0.1", 0)
                port = self._proxy.sockets[0].getsockname()[1]
                self._browser = await self._playwright.chromium.launch(
                    headless=True, proxy={"server": f"http://127.0.0.1:{port}"},
                    args=["--proxy-bypass-list=<-loopback>", "--dns-prefetch-disable",
                          "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"],
                )
                self.launch_ms += (time.perf_counter() - start) * 1000

    async def _deny_native(self, reader, writer):
        task = asyncio.current_task()
        self._proxy_tasks.add(task)
        try:
            async with asyncio.timeout(1):
                await reader.read(4096)
                writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                await writer.drain()
        except (OSError, TimeoutError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()
            self._proxy_tasks.discard(task)

    async def close(self):
        try:
            if self._browser:
                await self._browser.close()
        finally:
            if self._playwright:
                await self._playwright.stop()
            if self._proxy:
                self._proxy.close()
                await self._proxy.wait_closed()
                for task in list(self._proxy_tasks):
                    task.cancel()
                await asyncio.gather(*list(self._proxy_tasks), return_exceptions=True)

    async def fetch(self, url: str) -> FetchResult:
        outcome, reason = await self.network.allowed(url)
        if outcome != Outcome.OK:
            return FetchResult(url, outcome=outcome, reason=reason)
        async with self._slots, asyncio.timeout(self.config.browser_timeout + 2):
            start = time.perf_counter()
            context = None
            handlers: dict[asyncio.Task, dict] = {}
            settled: set[str] = set()
            refusals: dict[tuple[str, str, str], dict] = {}
            page_errors: list[str] = []
            closing = False
            primary = await self.network.fetch(url, role="browser_document", parent=url)
            if primary.outcome != Outcome.OK:
                return primary
            kind = primary.headers.get("content-type", "")
            if "json" in kind or "xml" in kind or "text/plain" in kind \
                    or "application/pdf" in kind or primary.body.startswith(b"%PDF-"):
                return primary
            initial = await asyncio.to_thread(Extractor().extract, primary)
            if initial.wall:
                primary.outcome, primary.reason = Outcome.BLOCKED, initial.reason
                return primary
            verifier = ContentVerifier()
            await asyncio.to_thread(verifier.apply, primary, initial, url)
            assessment = initial.best.verification if initial.best else None
            if assessment and not assessment.accepted and not assessment.recoverable:
                primary.outcome, primary.reason = Outcome.INCOMPLETE, assessment.reason
                primary.metadata["verification"] = verifier.metadata(assessment)
                return primary
            initial_content = initial.best is not None and initial.best.quality >= 0.5 \
                and assessment is not None and assessment.accepted
            redirected = {primary.url: primary}
            try:
                await self._start()
                context = await self._browser.new_context(
                    user_agent=self.config.user_agent, service_workers="block", accept_downloads=False,
                    viewport={"width": 1280, "height": 900}, java_script_enabled=True,
                )
                page = await context.new_page()
                page.on("pageerror", lambda error: page_errors.append(str(error)[:500])
                        if len(page_errors) < 8 else None)

                session = await context.new_cdp_session(page)
                frame = (await session.send("Page.getFrameTree"))["frameTree"]["frame"]["id"]
                self.pages += 1

                async def fail(event):
                    if event["requestId"] in settled:
                        return
                    settled.add(event["requestId"])
                    await finish_route(session.send("Fetch.failRequest", {
                        "requestId": event["requestId"], "errorReason": "Aborted"}))

                async def dispatch(event):
                    nonlocal primary
                    request = event["request"]
                    kind = event["resourceType"].lower()
                    if request["method"] not in {"GET", "HEAD"} or kind in {
                        "image", "media", "font", "websocket", "eventsource"} \
                            or (kind == "stylesheet" and not self.config.load_styles):
                        await fail(event)
                        return
                    target = canonicalize(request["url"])
                    if not target:
                        await fail(event)
                        return
                    main = kind == "document" and event["frameId"] == frame
                    headers = {key.lower(): value for key, value in request["headers"].items()
                               if key.lower() in {"accept", "accept-language", "origin", "referer"}}
                    result = redirected.pop(target) if target in redirected else await self.network.fetch(
                        target, role="browser_document" if main else "browser_asset", parent=url,
                        asset=not main, headers=headers, method=request["method"])
                    if main:
                        primary = result
                    if result.outcome not in {Outcome.OK, Outcome.UNCHANGED}:
                        key = (str(result.outcome), target.split("/", 3)[2], result.reason)
                        if key in refusals:
                            refusals[key]["count"] += 1
                        elif len(refusals) < 20:
                            refusals[key] = {"outcome": str(result.outcome), "reason": result.reason,
                                             "example_url": target, "count": 1}
                        await fail(event)
                        return
                    if main and result.url != target:
                        primary = FetchResult(target, outcome=Outcome.UNSUPPORTED,
                                              reason="Script navigation followed by HTTP redirect")
                        await fail(event)
                        return
                    response_headers = [{"name": k, "value": v} for k, v in result.headers.items() if k not in {
                        "content-encoding", "content-length", "transfer-encoding", "connection",
                        "set-cookie", "location", "content-security-policy-report-only"}]
                    settled.add(event["requestId"])
                    await finish_route(session.send("Fetch.fulfillRequest", {
                        "requestId": event["requestId"], "responseCode": result.status,
                        "responseHeaders": response_headers,
                        "body": base64.b64encode(result.body).decode(),
                    }))

                async def handle(event):
                    task = asyncio.current_task()
                    handlers[task] = event
                    try:
                        if closing:
                            await fail(event)
                        else:
                            await dispatch(event)
                    except asyncio.CancelledError:
                        try:
                            await fail(event)
                        except PlaywrightError:
                            pass
                    except PlaywrightError as exc:
                        if not closing and len(page_errors) < 8:
                            page_errors.append(str(exc)[:500])
                    finally:
                        handlers.pop(task, None)

                session.on("Fetch.requestPaused", handle)
                await session.send("Network.enable")
                await session.send("Network.setCacheDisabled", {"cacheDisabled": True})
                await session.send("Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]})
                await context.route_web_socket("**/*", lambda ws: ws.close())
                deadline = start + self.config.browser_timeout
                await page.goto(primary.url, wait_until="commit",
                                timeout=max(1, (deadline - time.monotonic()) * 1000))
                previous, stable_since = "", time.monotonic()
                settle = 0.3 if initial_content else self.config.render_wait
                readiness = "deadline"
                while time.monotonic() < deadline:
                    text = await page.evaluate(DOM_SNAPSHOT, False)
                    if text != previous:
                        stable_since = time.monotonic()
                    previous = text
                    enough_text = len(text) >= 80 or initial_content
                    if enough_text and time.monotonic() - stable_since >= settle:
                        snapshot = (await page.evaluate(DOM_SNAPSHOT, True)).encode()
                        rendered = FetchResult(primary.url, primary.status, snapshot,
                            {"content-type": "text/html; charset=utf-8"})
                        parsed = await asyncio.to_thread(Extractor().extract, rendered)
                        await asyncio.to_thread(verifier.apply, rendered, parsed, url)
                        best = parsed.best
                        if best and best.quality >= 0.5 and best.verification and best.verification.accepted:
                            readiness = "verified_initial_content" if initial_content else "verified_rendered_content"
                            break
                        if not handlers:
                            readiness = "settled_without_accepted_content"
                            break
                    await asyncio.sleep(0.15)
                scroll = await self._scroll(page, deadline, handlers, readiness == "deadline")
                content = (await page.evaluate(DOM_SNAPSHOT, True)).encode()
                if primary.outcome != Outcome.OK:
                    return primary
                if len(content) > self.config.max_bytes:
                    return FetchResult(url, outcome=Outcome.UNSUPPORTED, reason="Rendered DOM exceeds byte limit")
                return FetchResult(primary.url, primary.status, content,
                                   {**primary.headers, "content-type": "text/html; charset=utf-8"},
                                   (time.perf_counter() - start) * 1000,
                                   metadata={"browser": {
                                       "deadline_reached": time.monotonic() >= deadline,
                                       "readiness": readiness,
                                       "scroll": scroll,
                                       "native_egress": "deny-only local proxy",
                                       "pending_requests": [e["request"]["url"] for e in list(handlers.values())[:20]],
                                       "dependency_refusals": list(refusals.values()),
                                       "page_errors": page_errors,
                                   }})
            except PlaywrightError as exc:
                if primary and primary.outcome != Outcome.OK:
                    return primary
                return FetchResult(url, outcome=Outcome.ERROR,
                                   reason=f"Browser: {str(exc).splitlines()[0][:220]}",
                                   elapsed_ms=(time.perf_counter() - start) * 1000)
            finally:
                closing = True
                if context:
                    pending = list(handlers)
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    await context.close()
                    await asyncio.gather(*list(handlers), return_exceptions=True)
