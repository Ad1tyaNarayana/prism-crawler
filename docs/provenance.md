# Source provenance and similarity review

## Finding

The scoped October 2026 comparison found no evidence that Prism's reviewed source was copied from the selected upstream checkouts. This is a similarity finding, not proof of independent authorship or an exhaustive license audit.

Prism was developed with AI coding assistance. The available local Git history starts with a consolidated research commit, rather than an incremental history of the original build. Earlier source snapshots and experiment receipts help explain evolution but do not independently prove authorship.

The engine uses existing packages for HTTP, robots parsing, extraction, HTML/XML, Chromium and PDFs. In particular, Protego is from the Scrapy project. Using it is an explicit dependency, not evidence that the crawler is a renamed Scrapy fork. The project must retain that distinction when describing work as built from scratch.

## Method and scope

Pinned public source and test directories were checked out from:

- [Scrapy](https://github.com/scrapy/scrapy).
- [Crawlee for Python](https://github.com/apify/crawlee-python).
- [Crawl4AI](https://github.com/unclecode/crawl4ai).
- [Firecrawl](https://github.com/firecrawl/firecrawl).

The audit parses source without importing or executing upstream code. It checks exact source-file hashes, normalized ten-line windows containing at least 200 non-whitespace characters, and Python function AST shapes with at least 60 nodes after normalizing identifiers and constant values. Structural matches, if present, need human inspection; common programming patterns can match without copying.

[Machine-readable results](provenance-audit.json) record the precise local roots, counts, upstream commit hashes and matches. [Pinned upstreams](provenance-upstreams.json) and [the audit tool](../tools/audit_provenance.py) allow a reviewer to repeat the comparison after preparing matching sparse checkouts. Pass their checkout directory with `--reference-root`; the tool never fetches or runs reference code itself.

```sh
.venv/bin/python tools/audit_provenance.py \
  --upstreams docs/provenance-upstreams.json \
  --reference-root .audit/oss-upgrade/upstream \
  --local-root src --local-root tests --local-root benchmarks \
  --local-root experiments --local-root scripts --local-root tools \
  --local-root examples/local_crawl.py \
  --output .audit/provenance-recheck.json
```

The original checkouts stay local under `.audit/` and are excluded from Git/distributions. Firecrawl source inspected during earlier research also stayed under ignored raw reports. No upstream crawler source or model checkpoint is vendored in the distribution. Historical Prism snapshots in `examples/evidence/` are retained as evidence and excluded from wheel/sdist packaging.

## What this cannot establish

The audit covers selected repositories and revisions, not the entire internet or every older version. Renamed or transformed copies can evade its checks. It does not check all prose, all transitive/native dependency code, or third-party website content. Public search and local provenance evidence cannot conclusively rule out every source of generated code.

Before adding adapted source, identify the upstream file and license, preserve required notices, and record the adaptation. Do not assume Prism's MIT license applies to code copied from a differently licensed project. Current runtime dependencies retain their licenses in their own packages; [third-party notices](../THIRD_PARTY_NOTICES.md) lists the direct dependencies.
