# Preparing a release

The repository is private until the maintainer chooses to make it public. Nothing here automatically uploads to PyPI, creates a release, or changes visibility. The `prism-crawler` PyPI name was unregistered when checked during preparation; this does not reserve it.

1. Review [provenance](provenance.md), [third-party notices](../THIRD_PARTY_NOTICES.md), [security](../SECURITY.md) and the current Git history for source/content/credential issues.
2. Run the local checks in [CONTRIBUTING.md](../CONTRIBUTING.md), including wheel and sdist installation tests. Check dependency advisories and resolve actionable findings.
3. Run the configured Python/Linux/macOS CI matrix before claiming support across those environments. Local verification used Python 3.13 on macOS; a configured matrix is not evidence of passing runs.
4. Review README examples, supported API, known limits and the changelog. Treat experimental models and historical website coverage as development evidence.
5. Confirm the single version in `src/optimal_crawler/__init__.py`, package metadata and changelog agree. Keep release artifacts free of raw captures, model weights, keys and local databases.
6. If making the repository public, enable appropriate branch rules, dependency alerts, secret scanning and private vulnerability reporting where available. Confirm CI runs against the public repository. Set required checks after successful runs establish their names.
7. When deliberately publishing a package, verify ownership of the distribution name, prepare TestPyPI/PyPI trusted publishing, and test a TestPyPI install first. Publication requires a separate maintainer action; no upload workflow is configured here.

The verification workflow automatically runs on public pushes and pull requests. It also supports an explicit manual dispatch while private. Automatic jobs are skipped while private to avoid consuming a private Actions quota during this free/local preparation. Do not treat a skipped job as a passing test.

Current maturity is alpha. The project has bounded local recovery and source-content tests, but no long-duration production deployment, independent security review, Windows support or distributed reliability evidence.
