"""Fresh-fetch, matched-URL benchmark. Production crawler policy is unchanged."""

import argparse
import asyncio
import gzip
import hashlib
import json
import platform
import random
import re
import shutil
import ssl
import time
from dataclasses import asdict
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from unittest.mock import patch

import aiohttp
import certifi
from lxml import html

import optimal_crawler.crawler as crawler_module
from benchmarks.real50 import SOURCE_ROOT, SiteCase, runtime_digest, score
from benchmarks.real50_review import reviewed_checks
from optimal_crawler.browser import Browser
from optimal_crawler.crawler import Crawler
from optimal_crawler.extract import access_wall
from optimal_crawler.models import (
    Candidate,
    CrawlConfig,
    Document,
    Extraction,
    FetchResult,
    Outcome,
)
from optimal_crawler.network import Network
from optimal_crawler.urls import canonicalize
from optimal_crawler.verification import ContentVerifier

PROVIDERS = ("ours", "exa", "firecrawl")


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temporary.replace(path)


def content_check(case, document):
    return reviewed_checks({"documents": [document], "checks": score([case], [document])})[0]


def readable_text(text):
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    return re.sub(r"https?://\S+", "", text)


def quality_review(case, document, raw_html=None):
    text = document.get("text", "")
    check = content_check(case, document)
    plain_check = content_check(case, {**document, "text": readable_text(text)})
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    assessment = None
    if raw_html:
        result = FetchResult(document["final_url"], 200, raw_html.encode(),
                             {"content-type": "text/html"})
        verifier = ContentVerifier()
        candidate = Candidate("provider", text, document.get("title", ""), 1)
        assessment = verifier.metadata(verifier.assess(case.url, result, candidate,
                                                       verifier.source(result)))
    return {"check": check, "readable_check": plain_check, "source_assessment": assessment,
            "characters": len(text), "words": len(re.findall(r"\w+", text)),
            "duplicate_line_fraction": 1 - len(set(lines)) / max(1, len(lines)),
            "url_count": len(re.findall(r"https?://", text))}


def provider_document(provider, case, http_status, payload):
    document = {"url": canonicalize(case.url), "final_url": case.url, "outcome": "error",
                "method": provider, "title": "", "text": "", "reason": "", "status": http_status}
    freshness, raw_html, charge = "unknown", None, 0
    if provider == "exa":
        statuses = payload.get("statuses", [])
        status = next((s for s in statuses if s.get("id") == case.url), {})
        results = payload.get("results", [])
        result = results[0] if len(results) == 1 else next(
            (r for r in results if r.get("id") == case.url or r.get("url") == case.url), {})
        charge = payload.get("costDollars", {}).get("total", 0)
        freshness = status.get("source", "unknown")
        document.update(final_url=result.get("url", case.url), title=result.get("title", ""),
                        text=result.get("text", ""), reason=str(status.get("error") or payload.get("error") or ""))
        success = http_status == 200 and status.get("status") == "success"
    else:
        data = payload.get("data") or {}
        metadata = data.get("metadata", {})
        freshness = {"miss": "live", "hit": "cached"}.get(metadata.get("cacheState"), "unknown")
        raw_html = data.get("rawHtml")
        charge = metadata.get("creditsUsed", payload.get("creditsUsed", 0))
        document.update(final_url=metadata.get("url", case.url), title=metadata.get("title", ""),
                        text=data.get("markdown", ""), status=metadata.get("statusCode", http_status),
                        reason=str(payload.get("error") or ""))
        success = http_status == 200 and payload.get("success") is True
    if document["title"] and document["title"].casefold() not in document["text"].casefold():
        document["text"] = document["title"] + "\n" + document["text"]
    tree = html.fromstring(raw_html) if raw_html else html.fromstring(
        "<html><head><title>" + escape(document["title"]) + "</title></head><body>" +
        escape(document["text"]) + "</body></html>")
    wall = access_wall(tree) or document["status"] in (401, 403)
    if wall:
        document.update(outcome="access_wall", reason="Provider returned an access/challenge page")
    elif success and document["text"]:
        document["outcome"] = "ok"
    elif success:
        document["reason"] = "Provider success with empty text"
    return document, freshness, raw_html, charge


class BenchmarkNetwork(Network):
    async def allowed(self, url, asset=False, *, redirect=False):
        error = self.scope_error(url, asset, redirect=redirect)
        return (Outcome.OUT_OF_SCOPE, error) if error else (Outcome.OK, "")


class LiveBenchmark:
    def __init__(self, args):
        self.args, self.root = args, args.output
        self.bodies = self.root / "bodies"
        self.keys = {p: (args.keys / (p + ".key")).read_text().strip() for p in ("exa", "firecrawl")}
        self.stopped = {}
        self.spent = {"exa": 0.0, "firecrawl": 0.0}
        self.next_at = 0
        self.rows = []
        self.captures = []
        self.fingerprint = runtime_digest()
        self.session = None

    async def api_request(self, provider, endpoint, body=None):
        start = time.perf_counter()
        started_at = datetime.now(UTC).isoformat()
        status, data, headers, error = 0, b"", {}, ""
        try:
            async with self.session.request(
                "GET" if body is None else "POST", endpoint, json=body,
                headers={"Authorization": "Bearer " + self.keys[provider]},
            ) as response:
                status = response.status
                headers = {k.lower(): v for k, v in response.headers.items()
                           if k.lower() in {"retry-after", "x-ratelimit-limit", "x-ratelimit-remaining"}}
                chunks, size = [], 0
                async for chunk in response.content.iter_chunked(65536):
                    size += len(chunk)
                    if size > 24_000_000:
                        raise ValueError("Provider response exceeded benchmark byte limit")
                    chunks.append(chunk)
                data = b"".join(chunks)
                try:
                    payload = json.loads(data)
                except ValueError:
                    payload = {}
        except (aiohttp.ClientError, OSError, TimeoutError, ValueError) as exc:
            error, payload = type(exc).__name__, {}
        elapsed = (time.perf_counter() - start) * 1000
        sha = hashlib.sha256(data).hexdigest()
        if data:
            await asyncio.to_thread((self.bodies / (sha + ".gz")).write_bytes, gzip.compress(data, mtime=0))
        return {"provider": provider, "endpoint": endpoint, "request": body,
                "started_at": started_at, "elapsed_ms": elapsed, "http_status": status,
                "response_sha256": sha if data else None, "response_bytes": len(data),
                "headers": headers, "transport_error": error}, payload

    async def quota(self, name):
        receipt, payload = await self.api_request("firecrawl", "https://api.firecrawl.dev/v2/team/credit-usage")
        write_json(self.root / (name + ".json"), {"receipt": receipt, "response": payload})
        remaining = payload.get("data", {}).get("remainingCredits", 0)
        if receipt["http_status"] != 200 or remaining < 100:
            self.stopped["firecrawl"] = "Free-credit quota safety gate"
        return remaining

    async def provider(self, provider, case, repetition):
        path = self.root / f"run-{repetition}" / (provider + "-" + case.id + ".json")
        if provider in self.stopped:
            row = {"provider": provider, "id": case.id, "repetition": repetition,
                   "attempted": False, "reason": self.stopped[provider]}
        else:
            if self.spent[provider] >= (600 if provider == "firecrawl" else 1):
                self.stopped[provider] = "Predeclared free-credit spending cap"
                return await self.provider(provider, case, repetition)
            if provider == "firecrawl":
                endpoint = "https://api.firecrawl.dev/v2/scrape"
                body = {"url": case.url, "formats": ["markdown"], "maxAge": 0,
                        "proxy": "auto", "timeout": 90000, "onlyMainContent": True,
                        "skipTlsVerification": False, "parsers": [], "storeInCache": False}
            else:
                endpoint = "https://api.exa.ai/contents"
                body = {"ids": [case.url], "maxAgeHours": 0, "livecrawlTimeout": 90000,
                        "text": {"maxCharacters": 200000}}
            receipt, payload = await self.api_request(provider, endpoint, body)
            document, freshness, raw_html, charge = await asyncio.to_thread(
                provider_document, provider, case, receipt["http_status"], payload)
            self.spent[provider] += charge
            document["elapsed_ms"] = receipt["elapsed_ms"]
            review = await asyncio.to_thread(quality_review, case, document, raw_html)
            row = {"provider": provider, "id": case.id, "repetition": repetition,
                   "attempted": True, "receipt": receipt, "document": document,
                   "freshness": freshness, "charge": charge, **review}
            row["live_passed"] = freshness == "live" and review["check"]["passed"]
            if receipt["http_status"] in (401, 402, 403, 429):
                self.stopped[provider] = f"Service-level HTTP {receipt['http_status']}; no repeated calls"
        write_json(path, row)
        self.rows.append(row)
        return row

    async def run(self):
        cases = [SiteCase(**c) for c in json.loads(Path("benchmarks/real50.json").read_text())]
        assert len(cases) == len({c.id for c in cases}) == 50
        self.root.mkdir(parents=True, exist_ok=True)
        assert not (self.root / "phase.json").exists(), "Choose a new output directory"
        self.bodies.mkdir()
        profile = json.loads(Path("benchmarks/adaptive-profile.json").read_text())
        phase = {"started_at": datetime.now(UTC).isoformat(), "repetitions": self.args.repetitions,
                 "cases": 50, "runtime_sha256": self.fingerprint,
                 "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                 "manifest_sha256": hashlib.sha256(Path("benchmarks/real50.json").read_bytes()).hexdigest(),
                 "profile": profile, "benchmark_ignore_robots": self.args.benchmark_ignore_robots,
                 "production_source_changed": False, "per_provider_concurrency": 1,
                 "cohort_minimum_interval_seconds": self.args.interval,
                 "api_client_timeout_seconds": 100, "target_timeout_seconds": 90,
                 "firecrawl_proxy": "auto", "free_spending_caps": {"firecrawl_credits": 600, "exa_dollars": 1},
                 "host": {"platform": platform.platform(), "python": platform.python_version()},
                 "claim": "Five paired fresh-fetch trials per URL; compare observed quality and latency, not universal optimality.",
                 "limits": "Fresh provider fetch is not cold origin/CDN content. Vendor internal retries, rendering and policies are opaque. This is a reused development set. Queue pacing is outside per-request latency."}
        write_json(self.root / "phase.json", phase)
        shutil.copyfile(__file__, self.root / "harness.py")
        shutil.copyfile("benchmarks/real50.json", self.root / "manifest.json")
        shutil.copyfile("benchmarks/real50-review.json", self.root / "quality-contract.json")
        shutil.copytree(SOURCE_ROOT, self.root / "source", ignore=shutil.ignore_patterns("__pycache__"))
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_context, limit=4),
                                         timeout=aiohttp.ClientTimeout(total=100),
                                         cookie_jar=aiohttp.DummyCookieJar(), trust_env=False) as session:
            self.session = session
            balance = await self.quota("quota-before")
            assert balance >= 300, "Not enough verified free credits for the planned test"
            original_process, original_raw, original_browser = Crawler._process, Network._raw, Browser.fetch
            by_url = {canonicalize(c.url): c for c in cases}
            for repetition in range(self.args.repetitions):
                assert runtime_digest() == self.fingerprint, "Runtime changed during measurement"
                folder = self.root / f"run-{repetition}"
                folder.mkdir()
                self.captures = []

                async def capture(result, role, parent):
                    sha = hashlib.sha256(result.body).hexdigest() if result.body else None
                    self.captures.append({"url": result.url, "status": result.status, "role": role,
                                          "parent": parent, "body_sha256": sha,
                                          "_body": result.body,
                                          "headers": {k: v for k, v in result.headers.items()
                                                      if k in {"content-type", "location", "retry-after"}}})

                async def observed_raw(instance, url, headers, role, parent, asset=False,
                                       *, method="GET", redirect=False):
                    result = await original_raw(instance, url, headers, role, parent, asset,
                                                method=method, redirect=redirect)
                    if role != "browser_asset":
                        await capture(result, role, parent)
                    return result

                async def observed_browser(instance, url):
                    result = await original_browser(instance, url)
                    await capture(result, "rendered_dom", url)
                    return result

                async def observed_process(instance, url, repetition=repetition, folder=folder):
                    case = by_url[url]
                    capture_start = len(self.captures)
                    queued_at = time.perf_counter()
                    await asyncio.sleep(max(0, self.next_at - time.monotonic()))
                    queue_ms = (time.perf_counter() - queued_at) * 1000
                    self.next_at = time.monotonic() + self.args.interval

                    async def ours():
                        start = time.perf_counter()
                        try:
                            async with asyncio.timeout(90):
                                result = await original_process(instance, url)
                        except TimeoutError:
                            result = (Document(url, url, Outcome.ERROR, "adaptive",
                                               reason="90-second benchmark page deadline"),
                                      Extraction(), FetchResult(url, outcome=Outcome.ERROR))
                        elapsed = (time.perf_counter() - start) * 1000
                        document = asdict(result[0])
                        document["elapsed_ms"] = elapsed
                        review = quality_review(case, document)
                        row = {"provider": "ours", "id": case.id, "repetition": repetition,
                               "attempted": True, "document": document, "freshness": "live",
                               "elapsed_ms": elapsed, "queue_ms": queue_ms,
                               "live_passed": review["check"]["passed"], **review}
                        self.rows.append(row)
                        write_json(folder / ("ours-" + case.id + ".json"), row)
                        return result

                    order = list(PROVIDERS)
                    random.Random(f"{repetition}:{case.id}").shuffle(order)
                    tasks = {p: asyncio.create_task(ours() if p == "ours" else
                                                   self.provider(p, case, repetition)) for p in order}
                    results = await asyncio.gather(*(tasks[p] for p in order))
                    own_result = results[order.index("ours")]
                    for observed in self.captures[capture_start:]:
                        body = observed.pop("_body")
                        if body:
                            await asyncio.to_thread(
                                (self.bodies / (observed["body_sha256"] + ".gz")).write_bytes,
                                gzip.compress(body, mtime=0))
                    print(json.dumps({"event": "cohort", "run": repetition, "id": case.id,
                                      "finished": len([r for r in self.rows if r['repetition'] == repetition]),
                                      "spent": self.spent}), flush=True)
                    return own_result

                ordered = cases.copy()
                random.Random(230026 + repetition).shuffle(ordered)
                config = CrawlConfig(db=folder / "ours.db", max_pages=50, max_requests=2000,
                                     workers=1, browser_concurrency=1, min_delay=0.5, timeout=15,
                                     retries=1, render_wait=3, discover=False, follow_links=False,
                                     decision_provider="none", run_timeout=7200, **profile)
                network_type = BenchmarkNetwork if self.args.benchmark_ignore_robots else Network
                with patch.object(crawler_module, "Network", network_type), \
                     patch.object(Crawler, "_process", observed_process), \
                     patch.object(Network, "_raw", observed_raw), \
                     patch.object(Browser, "fetch", observed_browser):
                    async with Crawler(config) as crawler:
                        summary = await crawler.run([c.url for c in ordered])
                        write_json(folder / "ours-events.json", {
                            "summary": summary, "requests": crawler.store.events("requests", crawler.run_id),
                            "attempts": crawler.store.events("attempts", crawler.run_id),
                            "captures": self.captures, "config": config.to_dict()})
                await self.quota(f"quota-after-{repetition}")
                write_json(folder / "finished.json", {"rows": len([r for r in self.rows if r["repetition"] == repetition]),
                                                       "spent": self.spent, "stopped": self.stopped})
                print(json.dumps({"event": "round_finished", "run": repetition, "spent": self.spent}), flush=True)
        write_json(self.root / "finished.json", {"rows": len(self.rows), "spent": self.spent,
                                                "stopped": self.stopped, "finished_at": datetime.now(UTC).isoformat()})


async def main(args):
    try:
        await LiveBenchmark(args).run()
    finally:
        for provider in ("exa", "firecrawl"):
            (args.keys / (provider + ".key")).unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("reports/provider-live50"))
    parser.add_argument("--keys", type=Path, default=Path(".runtime/live50"))
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--interval", type=float, default=6.2)
    parser.add_argument("--benchmark-ignore-robots", action="store_true")
    asyncio.run(main(parser.parse_args()))
