"""Prism's public API for adaptive crawling with durable checkpoints."""

from optimal_crawler import __version__ as __version__
from optimal_crawler.models import CrawlConfig as CrawlConfig
from optimal_crawler.models import Document as Document
from optimal_crawler.models import Outcome as Outcome

from .client import AsyncCrawler as AsyncCrawler
from .client import CrawlResult as CrawlResult
from .client import CrawlStateError as CrawlStateError
from .client import RunSummary as RunSummary

__all__ = [
    "AsyncCrawler",
    "CrawlConfig",
    "CrawlResult",
    "CrawlStateError",
    "Document",
    "Outcome",
    "RunSummary",
    "__version__",
]
