# Third-party software and acknowledgments

Prism's own code is MIT licensed. Its dependencies retain their respective licenses. Dependencies are installed as packages, rather than vendored into Prism's source or wheel. Browser binaries and optional model weights are downloaded separately and are not included in this repository or its distributions.

Direct runtime dependencies, with licenses recorded from the installed metadata used for the October 2026 audit:

| Dependency | Purpose | Reported license | Upstream |
|---|---|---|---|
| aiohttp | Async HTTP client | Apache-2.0 AND MIT | https://github.com/aio-libs/aiohttp |
| certifi | CA certificate bundle | MPL-2.0 | https://github.com/certifi/python-certifi |
| trafilatura | Boilerplate removal candidate | Apache-2.0 | https://github.com/adbar/trafilatura |
| lxml | HTML and XML parsing | BSD-3-Clause | https://github.com/lxml/lxml |
| Protego | Robots rules | BSD-3-Clause | https://github.com/scrapy/protego |
| Playwright | Chromium automation | Apache-2.0 | https://github.com/microsoft/playwright-python |
| Typer | CLI | MIT | https://github.com/fastapi/typer |
| Rich | CLI presentation | MIT | https://github.com/Textualize/rich |
| pypdf | Bounded PDF extraction | BSD-3-Clause | https://github.com/py-pdf/pypdf |

The resolved dependency set is in `requirements-core.lock`. Transitive dependencies and native components have their own notices in their installed packages. This table does not replace those licenses or claim to cover every native component.

Laya and KEV are optional integrations, outside the default installation and evaluation. Their source and checkpoints retain their own licenses. The setup scripts identify the source/version and checkpoint used. A model integration does not transfer ownership of its code or weights to this project.

Scrapy, Crawlee, Crawl4AI and Firecrawl informed the design comparison and repository review. They are not dependencies of the crawler engine. The scoped source-similarity audit and its limitations are documented in [provenance](docs/provenance.md).

Historical benchmark manifests include public URLs. Captured website bodies are kept locally and excluded from distributions. Text in the local test fixtures is project-authored synthetic material. The MIT license is not a license to third-party website content.
