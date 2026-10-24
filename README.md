# Prism

[![Verification](https://github.com/Ad1tyaNarayana/prism-crawler/actions/workflows/test.yml/badge.svg)](https://github.com/Ad1tyaNarayana/prism-crawler/actions/workflows/test.yml)

**An async Python crawler with adaptive HTTP/Chromium retrieval, source-aware extraction and durable SQLite checkpoints.**

Prism starts with HTTP, uses guarded Chromium when content checks call for rendering, and returns documents together with explicit failure outcomes. Robots, origin scope, pacing and resource budgets apply to both paths. No hosted crawler, API key or decision model is required.

Status is **experimental alpha, version 0.2.0a1**. Install from this repository; the package has not been uploaded to PyPI. Python 3.11+ on macOS and Linux is targeted. See the verification workflow for current platform results. Expect API changes before 1.0.

## Install from source

```sh
git clone https://github.com/Ad1tyaNarayana/prism-crawler.git
cd prism-crawler
python3 -m venv .venv
.venv/bin/python -m pip install .
# Required for JavaScript rendering. HTTP retrieval works without the browser binary.
.venv/bin/python -m playwright install chromium
```

For the alpha release, check out the `v0.2.0a1` tag after cloning. The `main` branch contains ongoing development.

## Python library

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

Results remain usable after the context closes. `result.successful` and `result.failures` provide filtered documents; `result.summary` contains counts and costs. Page failures are data, while lifecycle misuse and invalid inputs raise errors. Each client owns one bounded run. Create a new client with the original configuration and seeds to resume an interrupted run.

See the [API reference](docs/api.md) for lifecycle, cancellation, outcomes, export behavior and limits.

## Command line

```sh
.venv/bin/prism crawl https://example.com --max-pages 10 --db data/example.db
.venv/bin/prism inspect --db data/example.db
.venv/bin/prism export --db data/example.db --output data/example.jsonl
# Equivalent module entry point.
.venv/bin/python -m prism_crawler --help
```

Repeat an interrupted crawl command with `--resume`. Each seed authorizes its origin. Additional public helpers require explicit `--redirect-origin` or `--asset-origin` options; their robots rules still apply. See [usage](docs/usage.md).

## Try it offline

```sh
.venv/bin/python examples/local_crawl.py --output data/local-example.jsonl
```

The example starts a local HTTP server, crawls a static and a JavaScript page, verifies their content, and checks that a robots-denied endpoint receives zero requests. It uses no public websites or hosted service.

For an actual recorded crash/restart demonstration:

```sh
PYTHONPATH=.:src .venv/bin/python -m benchmarks.demo --output reports/my-demo
.venv/bin/python -m http.server 8789 --bind 127.0.0.1
# Open http://127.0.0.1:8789/examples/evidence/ for the saved demo and results.
```

## What the engine supports

- Per-origin document queues and pacing, bounded dependency concurrency, robots checks, retry cooldowns and request/time/size budgets.
- HTML, selected structured text fields, feeds, sitemaps and bounded text PDFs; extraction candidates and detectable source-content gaps.
- Atomic page completion and discovered-child persistence, abandoned-lease recovery, conditional revalidation and heuristic revisit scheduling.
- Exact/near-duplicate annotations, explicit refusal/error outcomes and SQLite/JSONL telemetry.

This is a bounded single-machine library. Results materialize in memory. It has no distributed frontier, stable plugin API, custom request-handler framework, authentication automation or access-wall solver. Windows is unsupported. Quality checks are heuristic and can miss content gaps. Known extraction problems include escaped presentation markup in FastAPI terminal examples and navigation/newsletter noise on Fairphone. These remain open limitations; see [known extraction issues](docs/limits.md#known-extraction-issues). Read [architecture](docs/architecture.md), [limits](docs/limits.md) and [security](SECURITY.md) before embedding it in a service.

## Evidence and provenance

The historical frozen 50-hostname evaluation recorded **39/50 strict passes**, or **39/46 excluding four disclosed Cloudflare skips**. The reserved ten scored **5/10**. Those cohorts ran on two research source revisions; they are not a current-version accuracy claim.

An actual SIGKILL experiment exposed a frontier bug that lost 100 child URLs. Atomic page/child commits changed the same recovery test from **708/808 to 808/808**. A final research-source local load trial recovered **8,008/8,008** documents in **96.73 seconds**. These are synthetic local measurements, not internet capacity estimates. The new library interface is checked separately by behavioral and installed-artifact tests.

Read [evaluation](docs/evaluation.md), [saved receipts](examples/evidence/summary.json), [the source audit](docs/provenance.md) and [comparison with established projects](docs/comparison.md). The audit found no source matches in the scoped comparison; it does not prove originality across all public code. Prism uses existing parsing, networking and browser libraries, credited in [third-party notices](THIRD_PARTY_NOTICES.md).

A separate [local comparison with Crawlee and Crawl4AI](docs/oss-benchmark-results.md) uses 50 new URLs and source-reference content scoring. It separates the untouched holdout from later fixes, records browser/request costs, and preserves failed runs and label corrections. Read the [configurations and limits](docs/oss-benchmark-method.md) before interpreting it as a library ranking.

## Development and releases

```sh
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check src tests benchmarks tools examples
.venv/bin/python -m mypy
.venv/bin/python tools/check_links.py
.venv/bin/python -m build --outdir dist/release
.venv/bin/python -m twine check dist/release/*
.venv/bin/python tools/check_distribution.py --dist dist/release
```

See [CONTRIBUTING](CONTRIBUTING.md), [changelog](CHANGELOG.md), [release preparation](docs/releases.md) and [code of conduct](CODE_OF_CONDUCT.md). CI runs on public pushes and pull requests. Earlier automatic private runs skipped verification jobs; those skips are not passing checks. Dependency updates are configured through Dependabot. No upload workflow is configured.

The supported namespace is `prism_crawler`. `optimal_crawler` is the internal engine. `benchmarks/` and `experiments/` contain historical research, not stable library interfaces. Raw captures, provider credentials, model downloads and personal writing remain ignored.

## License

Prism's own code is [MIT licensed](LICENSE). Dependencies and third-party content retain their own licenses. Development used AI coding assistance; the repository preserves source, tests, measurements and reproducible checks.
