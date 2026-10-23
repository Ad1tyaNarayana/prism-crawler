"""Async client and detached results for bounded, persistent crawls."""

import asyncio
import json
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Literal, TypedDict, cast

from optimal_crawler.crawler import Crawler as _Engine
from optimal_crawler.models import CrawlConfig, Document, Outcome


class CrawlStateError(RuntimeError):
    """The client was used outside its context or for more than one crawl."""


class RunSummary(TypedDict):
    """Counters for the whole run, including failures and policy refusals."""

    run_id: str
    status: Literal["completed", "page_limit", "interrupted"]
    strategy: str
    pages: int
    outcomes: dict[str, int]
    elapsed_ms: float
    requests: int
    bytes: int
    browser_pages: int
    browser_startup_ms: float
    decision_calls: int
    decision_ms: float
    duplicates: int
    pending: int
    db: str


@dataclass(frozen=True)
class CrawlResult:
    """A detached run summary and source documents, usable after the client closes.

    Documents include refusals and failures. Their order is completion order.
    The tuple is fixed, but each Document and the summary remain mutable copies.
    """

    documents: tuple[Document, ...]
    summary: RunSummary

    @property
    def successful(self) -> tuple[Document, ...]:
        """Documents with usable content, including unchanged revalidations."""
        return tuple(d for d in self.documents if d.outcome in {Outcome.OK, Outcome.UNCHANGED})

    @property
    def failures(self) -> tuple[Document, ...]:
        """All documents without a successful content outcome."""
        return tuple(d for d in self.documents if d.outcome not in {Outcome.OK, Outcome.UNCHANGED})

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-serializable document and summary copies."""
        return asdict(self)

    def to_jsonl(self, path: str | Path) -> Path:
        """Write every document as UTF-8 JSONL, replacing the target file.

        This synchronous export includes failures and source metadata. Summary
        counters remain available separately through ``summary`` or ``to_dict``.
        """
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8") as file:
            for document in self.documents:
                file.write(json.dumps(asdict(document), ensure_ascii=False) + "\n")
        return destination


class AsyncCrawler:
    """Own one crawl and checkpoint database within an async context.

    Use a new context for another run. Resume an interrupted checkpoint by
    creating a new client with the same configuration and original seeds.
    Leaving the context cancels and joins any active crawl before closing its
    resources. Cancellation propagates to the caller and leaves resumable work.
    """

    def __init__(self, config: CrawlConfig | None = None) -> None:
        self.config = config or CrawlConfig()
        self._engine = _Engine(self.config)
        self._state: Literal["new", "opening", "open", "closing", "closed"] = "new"
        self._used = False
        self._active: asyncio.Task[Any] | None = None

    async def __aenter__(self) -> "AsyncCrawler":
        if self._state != "new":
            raise CrawlStateError("Create a new AsyncCrawler for each context")
        self._state = "opening"
        try:
            await self._engine.__aenter__()
        except BaseException:
            self._state = "closed"
            raise
        self._state = "open"
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._state != "open":
            return
        self._state = "closing"
        try:
            if self._active is not None:
                self._active.cancel()
                with suppress(asyncio.CancelledError):
                    await self._active
        finally:
            try:
                await self._engine.__aexit__(exc_type, exc, traceback)
            finally:
                self._state = "closed"

    async def crawl(self, seeds: str | Iterable[str], *, resume: bool = False) -> CrawlResult:
        """Crawl seed URLs, returning documents even for terminal page failures.

        A string means one seed URL. A client accepts one call only, including
        an interrupted call. Invalid seeds/configuration raise ValueError;
        lifecycle misuse raises CrawlStateError. Transport outcomes remain in
        Document.outcome. Caller cancellation raises asyncio.CancelledError.
        """
        if self._state != "open":
            raise CrawlStateError("Use AsyncCrawler inside 'async with'")
        if self._used:
            raise CrawlStateError("Create a new AsyncCrawler for each crawl")
        urls = [seeds] if isinstance(seeds, str) else list(seeds)
        if not urls or any(not isinstance(url, str) for url in urls):
            raise ValueError("Provide one or more HTTP or HTTPS seed URLs")
        self._used = True
        self._active = asyncio.current_task()
        try:
            summary = await self._engine.run(urls, resume=resume)
            assert self._engine.store is not None
            documents = tuple(
                Document(**{**row, "outcome": Outcome(row["outcome"])})
                for row in self._engine.store.documents(self._engine.run_id)
            )
            return CrawlResult(documents, cast(RunSummary, summary))
        finally:
            self._active = None
