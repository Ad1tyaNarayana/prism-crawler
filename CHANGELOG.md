# Changelog

The project uses semantic versioning. Before 1.0, minor versions can change the documented API; migration notes will accompany those changes. Patch versions are intended for compatible fixes. Experimental modules and the internal engine namespace have no compatibility promise.

## 0.2.0a1 (2026-10-24)

First public alpha. Install from the repository or GitHub release artifacts; no PyPI publication.

- Update pypdf to 6.21.2 for fixes to repeated file loading and malformed cross-reference handling.
- Document remaining FastAPI terminal-formatting and Fairphone boilerplate limitations.

- Add `prism_crawler.AsyncCrawler`, `CrawlResult`, `CrawlStateError` and typed result exports.
- Preserve the measured engine under `optimal_crawler` while exposing a documented library interface.
- Return detached documents after context closure, with JSONL export and explicit failure outcomes.
- Cancel and join active work on context exit, preserving resumable checkpoints.
- Add distribution metadata, MIT license, dependency credits, provenance review, contributor/security guidance and release checks.
- Add local public-API tests, installed wheel/sdist examples, typing checks and Python/macOS/Linux CI configuration.
- Preserve descriptive article cards inside main-content navigation instead of removing them as menus.
- Route empty regions owned by simple inline network-and-DOM-write scripts to guarded browser recovery, even when the introduction is substantial.
- Remove link-heavy legacy global header/footer containers while preserving article metadata and introductory prose.
- Preserve inline token adjacency and preformatted code whitespace; avoid decoding DOM text entities twice.
- Add a local comparison harness for pinned Crawlee and Crawl4AI configurations, source-reference quality scoring, browser/request receipts and explicit post-hoc label sensitivity analysis.

## 0.1.0 (local research prototype)

- Implement adaptive HTTP/Chromium retrieval, robots and scope enforcement, source verification and SQLite frontier persistence.
- Fix an observed crash window by committing page results and discovered children atomically.
- Preserve section/card headings and add bounded CMS prose extraction.
- Detect redirect cycles and record explicit refusal/error outcomes.
- Record frozen website experiments and local load/recovery evidence. These measurements predate the 0.2 library interface; see [evaluation](docs/evaluation.md).
