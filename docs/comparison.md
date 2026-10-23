# Reference projects and repository decisions

This is a design/repository comparison, not a performance or capability-parity benchmark. Primary documentation and source were reviewed during October 2026 preparation. Exact source revisions are in [the provenance audit](provenance-audit.json).

| Project | Relevant documented pattern | Decision for Prism |
|---|---|---|
| [Scrapy](https://docs.scrapy.org/en/latest/topics/practices.html) | Script/application embedding through crawler process/runner APIs | Provide a documented async client with explicit event-loop ownership and detached results |
| [Crawlee for Python](https://crawlee.dev/python/docs/quick-start) | HTTP and browser crawler classes with a shared interface; installable core/extras | Keep one public adaptive interface; document the browser binary installation separately |
| [Crawl4AI](https://github.com/unclecode/crawl4ai) | Async context-managed crawler and documented result objects; contributor/security guidance | Support context cleanup, typed content/failure outcomes and examples that work after closure |
| [Firecrawl](https://github.com/firecrawl/firecrawl) | Separate API/SDK surfaces and clear self-hosting repository documentation | Keep Prism's local library/CLI contract explicit and avoid implying hosted-service parity |

The reviewed repositories also provide packaging metadata, licenses, issue/contribution guidance, automated checks and release documentation. These are useful repository practices independent of a crawler's scale.

Prism now has a public import namespace, wheel and sdist checks, PEP 561 markers, focused public-API type checking, MIT license and dependency notices, contributor/security/conduct documents, issue forms, CODEOWNERS, a changelog and pinned-action CI. The test suite and examples use local origins. Public-site and provider benchmarks remain explicit research commands.

The CI matrix covers Python 3.11, 3.12 and 3.13 on Linux plus Python 3.13 on macOS. Those targets are configured, not all locally verified. Automatic private runs are skipped; manual private dispatch requires a deliberate maintainer action. No public/PyPI release is performed by a workflow.

There are substantial remaining differences. Prism does not offer Scrapy's mature extension ecosystem, a distributed service, a general request-handler framework, a stable plugin API or proven hosted-scale operations. SQLite ownership is single-process, exports materialize bounded results and the supported platform list excludes Windows. Repository polish does not establish production maturity.
