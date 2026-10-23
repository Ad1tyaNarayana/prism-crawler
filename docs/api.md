# Python API

The supported import namespace is `prism_crawler`. The distribution name is `prism-crawler`. Version 0.2 is pre-1.0; the current release is being prepared and has not been uploaded to PyPI. Install from this repository.

```python
import asyncio
from pathlib import Path
from prism_crawler import AsyncCrawler, CrawlConfig

async def main():
    config = CrawlConfig(db=Path("data/pages.db"), max_pages=10, max_requests=100)
    async with AsyncCrawler(config) as crawler:
        result = await crawler.crawl(["https://example.com/"])
    for document in result.documents:
        print(document.url, document.outcome, document.reason)
    result.to_jsonl("data/pages.jsonl")

asyncio.run(main())
```

HTTP-only pages do not need installed Chromium binaries. JavaScript fallback requires `python -m playwright install chromium`. The Python Playwright dependency is installed with the core. A missing browser is recorded as a page failure, not an instruction to bypass network policy.

## AsyncCrawler

`AsyncCrawler(config: CrawlConfig | None = None)` owns one bounded crawl and its checkpoint database. Use it as an async context manager. The default configuration writes to `data/crawl.db`; specify another `Path` for independent work.

`await crawler.crawl(seeds, resume=False) -> CrawlResult` accepts a URL string or iterable of strings. Each seed authorizes its origin. Configuration limits, robots, DNS restrictions and explicit exclusions still apply. Page outcomes are included in the result, even if every page fails. To perform another run, create a new context. Overlapping runs on one client are rejected. The method materializes results in memory and is intended for bounded runs; it is not a streaming API.

Leaving the context cancels and joins an active crawl before releasing its resources. Caller cancellation raises `asyncio.CancelledError` and leaves recoverable state. Reopen an interrupted database with the original configuration and original seeds, then pass `resume=True`. A completed page-limit run is not an interrupted run. Increasing limits does not resume that run; start a new one.

## CrawlConfig

A frozen dataclass with validated budgets and policy settings. The exact fields and defaults are visible in its Python signature. Common options:

| Field | Default | Meaning |
|---|---:|---|
| `db` | `Path("data/crawl.db")` | Persistent checkpoint and telemetry |
| `max_pages` | 50 | Maximum recorded URL outcomes, including refusals |
| `max_requests` | 500 | Global request budget, including robots and dependencies |
| `workers` | 4 | Work across independent origins |
| `min_delay` | 0.5 | Document pacing in seconds |
| `timeout` | 20 | Individual request timeout in seconds |
| `browser_timeout` | 90 | Browser page budget after slot acquisition |
| `run_timeout` | 600 | Overall crawl deadline in seconds |
| `discover` / `follow_links` | `True` / `True` | Sitemap/feed discovery and page link traversal |
| `asset_origins` | `()` | Additional browser GET dependency origins |
| `redirect_origins` | `()` | Explicit redirect helper origins |
| `allow_private` | `False` | Permit private/local destinations in trusted environments |

Use `dataclasses.replace(config, ...)` for a modified copy. `db` is a `pathlib.Path`. One process owns a database at a time. Multiple independent clients need separate database paths. POSIX locking limits current support to macOS and Linux.

## CrawlResult and Document

`result.documents` is a tuple of detached `Document` objects in completion order. `successful` filters `ok` and `unchanged`. `failures` returns all other outcomes. Documents remain readable after the context closes. Their fields and the summary are mutable copies; changing them does not update the saved database.

Each document includes `url`, `final_url`, `outcome`, `method`, `text`, `title`, `parser`, `quality`, `status`, `reason`, `elapsed_ms`, `duplicate_of`, `next_due` and `metadata`. Verification metadata means no gap was detected by the implemented checks; it does not certify completeness. Duplicates are annotated and retained.

`result.summary` is a `RunSummary` typed dictionary with run status, outcome counts, pages, requests, bytes, elapsed time, browser/model costs, duplicate count, pending count and database path. Status is `completed`, `page_limit`, or `interrupted`. The run's elapsed time and document elapsed times have different scopes; see the measurement notes in [evaluation](evaluation.md).

`result.to_dict()` returns serializable document and summary copies. `result.to_jsonl(path)` synchronously overwrites the target with one UTF-8 record per document, including failures, and returns its `Path`. The summary is not included in the JSONL file. Export it separately from `summary` if needed.

## Outcomes and errors

`Outcome` is a string enum. Successful values are `OK` (`ok`) and `UNCHANGED` (`unchanged`). Other values distinguish `ROBOTS`, `ROBOTS_UNAVAILABLE`, `BLOCKED`, `OUT_OF_SCOPE`, `UNSUPPORTED`, `LOW_QUALITY`, `INCOMPLETE`, `ERROR`, `CANCELLED` and `BUDGET`.

Transport and extraction failures usually become document outcomes. `ValueError` reports invalid seeds/configuration or a resume mismatch. `CrawlStateError` reports context reuse, calls outside a context and repeated/overlapping crawl calls. Database ownership raises `RuntimeError`; local filesystem/SQLite failures propagate. Cancellation propagates as `asyncio.CancelledError`. An internal worker failure can propagate as a Python `ExceptionGroup`; its checkpoint remains interrupted.

The low-level `optimal_crawler` namespace, SQLite schema, experimental strategy switches and historical benchmark modules are internal interfaces. There is no plugin, distributed-worker or custom request-handler API in this release.
