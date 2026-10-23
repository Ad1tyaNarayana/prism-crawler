from dataclasses import asdict, dataclass, field
from enum import StrEnum
from math import isfinite
from pathlib import Path
from typing import Any


class Strategy(StrEnum):
    HTTP = "http"
    BROWSER = "browser"
    ADAPTIVE = "adaptive"


class Outcome(StrEnum):
    OK = "ok"
    UNCHANGED = "unchanged"
    ROBOTS = "robots_denied"
    ROBOTS_UNAVAILABLE = "robots_unavailable"
    BLOCKED = "access_wall"
    OUT_OF_SCOPE = "out_of_scope"
    UNSUPPORTED = "unsupported"
    LOW_QUALITY = "low_quality"
    INCOMPLETE = "incomplete"
    ERROR = "error"
    CANCELLED = "cancelled"
    BUDGET = "budget_exhausted"


@dataclass(frozen=True)
class CrawlConfig:
    db: Path = Path("data/crawl.db")
    strategy: Strategy = Strategy.ADAPTIVE
    max_pages: int = 50
    max_requests: int = 500
    max_frontier: int = 5000
    max_depth: int = 4
    workers: int = 4
    browser_concurrency: int = 2
    asset_concurrency: int = 4
    asset_delay: float = 0.05
    min_delay: float = 0.5
    max_delay: float = 30.0
    timeout: float = 20.0
    browser_timeout: float = 90.0
    run_timeout: float = 600.0
    render_wait: float = 2.5
    scroll_steps: int = 6
    load_styles: bool = True
    max_bytes: int = 16_000_000
    retries: int = 2
    discover: bool = True
    follow_links: bool = True
    allow_private: bool = False
    asset_origins: tuple[str, ...] = ()
    redirect_origins: tuple[str, ...] = ()
    excluded_origins: tuple[str, ...] = ()
    user_agent: str = "PrismCrawler/0.1 (+local research crawler)"
    decision_provider: str = "none"
    decision_endpoint: str = "http://127.0.0.1:8009"
    decision_model: str = "kev-latest"
    laya_model: str = "multilingual"
    decision_mode: str = "shadow"
    decision_threshold: float = 0.9
    recrawl_min: float = 3600
    recrawl_max: float = 30 * 86400
    initial_interval: float = 86400

    def __post_init__(self) -> None:
        for name in ("min_delay", "asset_delay", "max_delay", "timeout", "browser_timeout", "run_timeout", "render_wait",
                     "recrawl_min", "recrawl_max", "initial_interval"):
            if not isfinite(getattr(self, name)):
                raise ValueError(f"{name} must be finite")
        for name in ("max_pages", "max_requests", "max_frontier", "workers",
                     "browser_concurrency", "max_bytes"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be positive")
        if self.min_delay < 0 or self.max_delay < self.min_delay:
            raise ValueError("Require 0 <= min_delay <= max_delay")
        if not 1 <= self.asset_concurrency <= 16 or not 0 <= self.asset_delay <= self.max_delay:
            raise ValueError("Require 1 <= asset_concurrency <= 16 and 0 <= asset_delay <= max_delay")
        if min(self.timeout, self.browser_timeout, self.run_timeout) <= 0 or self.render_wait < 0:
            raise ValueError("Timeouts must be positive and render_wait non-negative")
        if self.retries < 0 or self.max_depth < 0:
            raise ValueError("Retries and depth cannot be negative")
        if not 0 <= self.scroll_steps <= 20:
            raise ValueError("scroll_steps must be between 0 and 20")
        if self.decision_provider not in {"none", "laya", "systemone"}:
            raise ValueError("decision_provider must be none, laya, or systemone")
        if self.decision_mode not in {"shadow", "assist"}:
            raise ValueError("decision_mode must be shadow or assist")
        if self.laya_model not in {"english", "multilingual", "auto"}:
            raise ValueError("laya_model must be english, multilingual, or auto")
        if not 0 <= self.decision_threshold <= 1:
            raise ValueError("decision_threshold must be a probability")
        if not 0 < self.recrawl_min <= self.initial_interval <= self.recrawl_max:
            raise ValueError("Require 0 < recrawl_min <= initial_interval <= recrawl_max")

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "db": str(self.db)}


@dataclass
class FetchResult:
    url: str
    status: int = 0
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    elapsed_ms: float = 0
    outcome: Outcome = Outcome.OK
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        content_type = self.headers.get("content-type", "")
        charset = content_type.split("charset=")[-1].split(";")[0].strip() \
            if "charset=" in content_type else "utf-8"
        try:
            return self.body.decode(charset, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")


class VerificationIssue(StrEnum):
    MISSING_SECTIONS = "missing_sections"
    MISSING_CONTENT = "missing_content"
    LOADING_SHELL = "loading_shell"
    TARGET_IDENTIFIER = "target_identifier_changed"
    DEFERRED_CONTENT = "deferred_content"
    SOURCE_TRUNCATED = "source_truncated"


@dataclass(frozen=True)
class SourceEvidence:
    scope: str = "unavailable"
    headings: tuple[str, ...] = ()
    blocks: tuple[str, ...] = ()
    loading_shell: bool = False
    limited: bool = False
    deferred_content: bool = False


@dataclass(frozen=True)
class Verification:
    issues: tuple[VerificationIssue, ...] = ()
    checks: tuple[str, ...] = ()
    missing_headings: tuple[str, ...] = ()
    checked_blocks: int = 0
    covered_blocks: int = 0
    evidence_scope: str = "unavailable"
    evidence_limited: bool = False

    @property
    def accepted(self) -> bool:
        return not self.issues

    @property
    def recoverable(self) -> bool:
        return not self.accepted and not set(self.issues).intersection({
            VerificationIssue.TARGET_IDENTIFIER, VerificationIssue.SOURCE_TRUNCATED})

    @property
    def reason(self) -> str:
        reasons = {
            VerificationIssue.MISSING_SECTIONS: "Extraction omits observed main-content sections",
            VerificationIssue.MISSING_CONTENT: "Extraction omits observed substantive source blocks",
            VerificationIssue.LOADING_SHELL: "Metadata fragment does not resolve the executable loading shell",
            VerificationIssue.TARGET_IDENTIFIER: "Redirect lost the requested resource identifier",
            VerificationIssue.DEFERRED_CONTENT: "Source still advertises unfinished content",
            VerificationIssue.SOURCE_TRUNCATED: "Source extraction reached a content or traversal limit",
        }
        return "; ".join(reasons[issue] for issue in self.issues)


@dataclass
class Candidate:
    parser: str
    text: str
    title: str
    quality: float
    metadata: dict[str, Any] = field(default_factory=dict)
    verification: Verification | None = None


@dataclass
class Extraction:
    candidates: list[Candidate] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    alternatives: list[str] = field(default_factory=list)
    feeds: list[str] = field(default_factory=list)
    needs_js: bool = False
    wall: bool = False
    reason: str = ""
    evidence: SourceEvidence | None = None
    unsupported: bool = False

    @property
    def best(self) -> Candidate | None:
        return max(self.candidates, key=lambda c: (
            c.quality >= 0.5 and (c.verification is None or c.verification.accepted), c.quality), default=None)


@dataclass
class Document:
    url: str
    final_url: str
    outcome: Outcome
    method: str
    text: str = ""
    title: str = ""
    parser: str = ""
    quality: float = 0
    reason: str = ""
    status: int = 0
    elapsed_ms: float = 0
    duplicate_of: str | None = None
    next_due: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Attempt:
    url: str
    method: str
    outcome: Outcome
    elapsed_ms: float
    quality: float = 0
    reason: str = ""

