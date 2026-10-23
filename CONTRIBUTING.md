# Contributing

Prism is an early-stage, single-machine Python crawler. Bug reports, reproducible extraction failures and small improvements are welcome. Start with an issue for a substantial API or architecture change.

## Development setup

Python 3.11+ on macOS or Linux is required. Windows is currently unsupported because checkpoint ownership uses POSIX file locks.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m playwright install chromium
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check src tests benchmarks tools examples
.venv/bin/python -m mypy
```

Tests run against local HTTP servers. Do not add public-site requests, API keys or hosted providers to the default suite. Reproduce bugs with a minimal local source page where possible. Assert output content and origin-side request behavior, rather than only mocking internal calls.

## Pull requests

Explain the user-visible problem, the change and how you checked it. Include a regression when it demonstrates a real failure. Preserve frozen benchmark expectations and original receipts; write a new output directory for reruns. A passing keyword check is insufficient evidence of complete extraction.

Keep the default adaptive engine under `src/optimal_crawler/`. The supported library interface is `src/prism_crawler/`; add public exports deliberately and document lifecycle and error behavior. Historical experiment scripts are not stable APIs.

Changes to scope, robots, redirects, DNS resolution, cancellation or checkpoint writes need tests showing the relevant request or recovery behavior. Read [architecture](docs/architecture.md) and [security policy](SECURITY.md) before changing these boundaries.

## Distribution checks

```sh
.venv/bin/python -m build --outdir dist/release
.venv/bin/python -m twine check dist/release/*
.venv/bin/python tools/check_distribution.py --dist dist/release
.venv/bin/python tools/check_links.py
.venv/bin/python -m pip_audit -r requirements-core.lock --no-deps --disable-pip
```

The distribution check creates temporary environments and installs the artifacts with their dependencies. It runs the offline example from outside the checkout and verifies public imports and installed package metadata.

## Contributions and conduct

By contributing, you agree to license your contribution under this project's MIT license and confirm you have the right to submit it. Credit any adapted third-party code and preserve its license obligations. Describe AI assistance when it materially affects how a change should be reviewed. Review generated code and run the same checks required for other contributions.

Treat other contributors with respect. Follow [the code of conduct](CODE_OF_CONDUCT.md). There is no response-time or support-level guarantee.
