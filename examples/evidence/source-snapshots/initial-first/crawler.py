import asyncio
import fcntl
import json
import time
from collections import Counter

from .browser import Browser
from .decisions import ROUTE_QUESTION, DecisionEngine
from .extract import Extractor, page_like, xml_discovery
from .models import (
    Attempt,
    CrawlConfig,
    Document,
    Extraction,
    FetchResult,
    Outcome,
    SourceEvidence,
    Strategy,
)
from .network import Network
from .recovery import can_recover, should_render
from .store import Store
from .urls import canonicalize, origin
from .verification import VERIFIER_VERSION, ContentVerifier, normalize


class Crawler:
    def __init__(self, config: CrawlConfig | None = None):
        self.config = config or CrawlConfig()
        self.store: Store | None = None
        self.network: Network | None = None
        self.browser: Browser | None = None
        self.decisions: DecisionEngine | None = None
        self.extractor = Extractor()
        self.verifier = ContentVerifier()
        self.run_id = ""
        self._lock_file = None
        self._discovered: set[str] = set()
        self._origins: set[str] = set()

    async def __aenter__(self):
        self.config.db.parent.mkdir(parents=True, exist_ok=True)
        self._lock_file = open(str(self.config.db) + ".lockfile", "a+")
        try:
            fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._lock_file.close()
            raise RuntimeError("Another crawler owns this database") from exc
        try:
            self.store = Store(self.config.db)
        except BaseException:
            self._lock_file.close()
            raise
        return self

    async def __aexit__(self, *_):
        try:
            if self.browser:
                await self.browser.close()
        finally:
            try:
                if self.network:
                    await self.network.__aexit__()
            finally:
                try:
                    if self.store:
                        self.store.close()
                finally:
                    if self._lock_file:
                        self._lock_file.close()

    def _enqueue(self, url: str, depth: int) -> bool:
        clean = canonicalize(url)
        if not clean or origin(clean) not in self._origins or not page_like(clean):
            return False
        if depth > self.config.max_depth:
            return False
        return self.store.enqueue(self.run_id, clean, depth, self.config.max_frontier)

    async def _discover(self, seed: str):
        domain = origin(seed)
        if domain in self._discovered:
            return
        self._discovered.add(domain)
        outcome, _ = await self.network.allowed(seed)
        if outcome != Outcome.OK:
            return
        robots = await self.network.robots(seed)
        pending = list(dict.fromkeys([*robots.sitemaps, domain + "/sitemap.xml"]))
        visited = set()
        while pending and len(visited) < 12:
            url = canonicalize(pending.pop(0), seed)
            if not url or url in visited or origin(url) not in self._origins:
                continue
            visited.add(url)
            result = await self.network.fetch(url, role="discovery", parent=seed)
            if result.outcome != Outcome.OK:
                continue
            urls, nested = xml_discovery(result.body, result.url, self.config.max_bytes)
            for discovered in urls:
                self._enqueue(discovered, 1)
            pending.extend(nested)

    async def run(self, seeds: list[str], *, resume: bool = False) -> dict:
        if self.store is None:
            raise RuntimeError("Use Crawler as an async context manager")
        if self.run_id:
            raise RuntimeError("Create a new Crawler context for each run")
        clean_seeds = list(dict.fromkeys(canonicalize(url) for url in seeds))
        if not clean_seeds or None in clean_seeds:
            raise ValueError("Provide valid HTTP or HTTPS seed URLs without credentials")
        self._origins = {origin(url) for url in clean_seeds}
        self.run_id = self.store.start(self.config, clean_seeds, resume)
        self.network = await Network(self.config, self.store, self.run_id, self._origins).__aenter__()
        self.browser = Browser(self.config, self.network)
        self.decisions = DecisionEngine(self.config, self.store, self.run_id)
        for url in clean_seeds:
            self._enqueue(url, 0)
        start = time.perf_counter()
        completed = len(self.store.documents(self.run_id))
        busy: set[str] = set()
        claimed = completed
        status = "completed"

        async def worker():
            nonlocal claimed
            while claimed < self.config.max_pages:
                item = self.store.claim(self.run_id, busy)
                if not item:
                    if not busy:
                        return
                    await asyncio.sleep(0.02)
                    continue
                busy.add(item["origin"])
                claimed += 1
                try:
                    document, extraction, result = await self._process(item["url"])
                    self.store.record(self.run_id, document, self.config,
                                      result.headers.get("etag", ""), result.headers.get("last-modified", ""))
                    if self.config.follow_links and document.outcome in {Outcome.OK, Outcome.LOW_QUALITY}:
                        for url in extraction.links:
                            self._enqueue(url, item["depth"] + 1)
                    if self.config.discover and document.outcome == Outcome.OK:
                        for feed in extraction.feeds[:2]:
                            self._enqueue(feed, item["depth"] + 1)
                finally:
                    busy.discard(item["origin"])

        try:
            async with asyncio.timeout(self.config.run_timeout):
                if self.config.discover:
                    await asyncio.gather(*(self._discover(seed) for seed in clean_seeds))
                async with asyncio.TaskGroup() as group:
                    for _ in range(self.config.workers):
                        group.create_task(worker())
        except TimeoutError:
            status = "interrupted"
        except BaseException:
            self.store.finish(self.run_id, "interrupted", {"reason": "Run interrupted; leases recover on resume"})
            raise
        duration = (time.perf_counter() - start) * 1000
        documents = self.store.documents(self.run_id)
        requests = self.store.events("requests", self.run_id)
        if status == "completed" and self.store.pending(self.run_id):
            status = "page_limit"
        outcomes = dict(Counter(d["outcome"] for d in documents))
        summary = {
            "run_id": self.run_id, "status": status, "strategy": str(self.config.strategy),
            "pages": len(documents), "outcomes": outcomes, "elapsed_ms": round(duration, 2),
            "requests": len(requests), "bytes": sum(r["bytes"] for r in requests),
            "browser_pages": self.browser.pages, "browser_startup_ms": round(self.browser.launch_ms, 2),
            "decision_calls": self.decisions.calls, "decision_ms": round(self.decisions.elapsed_ms, 2),
            "duplicates": sum(d["duplicate_of"] is not None for d in documents),
            "pending": self.store.pending(self.run_id), "db": str(self.config.db),
        }
        self.store.finish(self.run_id, status, summary)
        return summary

    async def _attempt(self, url: str, method: str, headers: dict | None = None,
                       reference: SourceEvidence | None = None):
        start = time.perf_counter()
        if method == "browser":
            try:
                result = await self.browser.fetch(url)
            except TimeoutError:
                result = FetchResult(url, outcome=Outcome.ERROR, reason="Browser page deadline exceeded")
        else:
            result = await self.network.fetch(url, headers=headers, role=method)
        extraction = Extraction()
        if result.outcome == Outcome.OK:
            extraction = await asyncio.to_thread(self.extractor.extract, result)
            if extraction.wall:
                result.outcome, result.reason = Outcome.BLOCKED, extraction.reason
            elif extraction.unsupported:
                result.outcome, result.reason = Outcome.UNSUPPORTED, extraction.reason
            else:
                for candidate in extraction.candidates:
                    if candidate.title and normalize(candidate.title) not in normalize(candidate.text):
                        candidate.text = candidate.title + "\n" + candidate.text
                await asyncio.to_thread(self.verifier.apply, result, extraction, url, reference)
        candidate = extraction.best
        outcome = result.outcome
        if outcome == Outcome.OK and candidate and candidate.verification and not candidate.verification.accepted:
            outcome = Outcome.INCOMPLETE
            extraction.reason = candidate.verification.reason
        elif outcome == Outcome.OK and (not candidate or candidate.quality < 0.5):
            outcome = Outcome.LOW_QUALITY
        elapsed = (time.perf_counter() - start) * 1000
        self.store.attempt(self.run_id, Attempt(url, method, outcome, elapsed,
                                               candidate.quality if candidate else 0,
                                               result.reason or extraction.reason))
        return result, extraction, outcome

    async def _process(self, url: str) -> tuple[Document, Extraction, FetchResult]:
        start = time.perf_counter()
        previous = self.store.previous(url)
        headers = {}
        if previous:
            old = json.loads(previous["document"])
            verified = old.get("metadata", {}).get("verification", {}).get("version") == VERIFIER_VERSION
            if old["method"] == "http" and old["final_url"] == url and verified:
                if previous["etag"]:
                    headers["If-None-Match"] = previous["etag"]
                if previous["modified"]:
                    headers["If-Modified-Since"] = previous["modified"]
        method = "browser" if self.config.strategy == Strategy.BROWSER else "http"
        learned = self.config.strategy == Strategy.ADAPTIVE and self.store.learned_browser(url)
        if learned:
            method = "browser"
        browser_attempted = method == "browser"
        result, extraction, outcome = await self._attempt(url, method, headers)
        if outcome == Outcome.UNCHANGED:
            if previous and headers:
                document = Document(**json.loads(previous["document"]))
                document.outcome = Outcome.UNCHANGED
                document.elapsed_ms = (time.perf_counter() - start) * 1000
                document.reason = "Server validated the stored representation with HTTP 304"
                result.headers.setdefault("etag", previous["etag"])
                result.headers.setdefault("last-modified", previous["modified"])
                return document, extraction, result
            outcome, result.reason = Outcome.ERROR, "HTTP 304 without a currently verified conditional representation"
        assessment = extraction.best.verification if extraction.best else None
        if method == "browser" and (outcome in {Outcome.ERROR, Outcome.LOW_QUALITY}
                                    or can_recover(outcome, assessment)):
            method = "http"
            result, extraction, outcome = await self._attempt(url, method, headers)
        assessment = extraction.best.verification if extraction.best else None
        if self.config.strategy == Strategy.ADAPTIVE and can_recover(outcome, assessment):
            reference = extraction.evidence
            for alternative in extraction.alternatives[:3]:
                if origin(alternative) != origin(url):
                    continue
                alternative_result, alternative_extraction, alternative_outcome = await self._attempt(
                    alternative, "api", reference=reference)
                if alternative_outcome == Outcome.OK:
                    result, extraction, outcome = alternative_result, alternative_extraction, alternative_outcome
                    method = "api"
                    break
            assessment = extraction.best.verification if extraction.best else None
            if can_recover(outcome, assessment) and not browser_attempted:
                state = {"title": extraction.best.title if extraction.best else "",
                         "visible_text": extraction.best.text[:1200] if extraction.best else "",
                         "has_javascript_shell": extraction.needs_js,
                         "html_excerpt": result.text[:1200]}
                answer = await self.decisions.decide(state, ROUTE_QUESTION, "route", url)
                model_requests_browser = False
                if answer and self.config.decision_mode == "assist":
                    choice = answer["answers"]["route"]
                    model_requests_browser = choice["choice"] == "A" \
                        and choice["probabilities"]["A"] >= self.config.decision_threshold
                if should_render(outcome, assessment, extraction.needs_js,
                                 browser_attempted, model_requests_browser):
                    browser_attempted = True
                    method = "browser"
                    result, extraction, outcome = await self._attempt(url, method)
        eligible = sorted((c for c in extraction.candidates if c.quality >= 0.5
                           and c.verification and c.verification.accepted),
                          key=lambda c: c.quality, reverse=True)
        candidate = await self.decisions.select(url, eligible) \
            if self.config.strategy == Strategy.ADAPTIVE and outcome == Outcome.OK else extraction.best
        document = Document(
            url=url, final_url=result.url, outcome=outcome, method=method,
            text=candidate.text if candidate and outcome == Outcome.OK else "",
            title=candidate.title if candidate else "", parser=candidate.parser if candidate else "",
            quality=candidate.quality if candidate else 0, status=result.status,
            reason=result.reason or extraction.reason or ("No extraction candidate reached quality threshold"
                                                          if outcome == Outcome.LOW_QUALITY else ""),
            elapsed_ms=(time.perf_counter() - start) * 1000,
            metadata={"route": "learned" if learned else "cold", "source_url": result.url,
                      "candidate_parsers": [c.parser for c in extraction.candidates],
                      **result.metadata,
                      **(candidate.metadata if candidate else {}),
                      **({"verification": self.verifier.metadata(candidate.verification)}
                         if candidate and candidate.verification else {})},
        )
        return document, extraction, result
