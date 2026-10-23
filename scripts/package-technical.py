"""Package runnable sources and measured evidence without environments or databases."""

import hashlib
import json
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
destination = root / "dist/prism-crawler-technical.zip"
if destination.exists():
    raise SystemExit("Archive already exists; choose a new name to preserve it")
verified = json.loads((root / "reports/technical-final/verification.json").read_text())
assert verified["verified"] and verified["documents"] == 825
inputs = [root / name for name in ("src", "tests", "benchmarks", "docs", "scripts", "reports", ".audit")]
files = [p for base in inputs for p in base.rglob("*") if p.is_file()]
files += [root / name for name in ("README.md", "pyproject.toml", ".gitignore",
                                  "requirements-core.lock", "requirements-models.lock", "requirements-kev.lock")]
files += list((root / "data").glob("*.jsonl"))
excluded_parts = {"__pycache__", "real50-candidate", ".pytest_cache", ".ruff_cache"}
with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
    count = 0
    for path in sorted(set(files)):
        relative = path.relative_to(root)
        if excluded_parts.intersection(relative.parts) or path.suffix in {".db", ".pstats", ".pyc"} \
                or ".db-" in path.name or path.name.endswith(".lockfile"):
            continue
        archive.write(path, Path("prism-crawler") / relative)
        count += 1
    archive.writestr("prism-crawler/ARCHIVE-NOTES.txt",
                    "Runnable source, dependency locks, tests, docs, full measured evidence, development "
                    "experiments and decision logs. Virtual environments, model weights, SQLite databases "
                    "and binary CPU profiles are excluded. Install with bash scripts/setup.sh. "
                    "Final results are under reports/technical-final/. First technical-round results "
                    "are under reports/technical/. Historical results are retained. "
                    "The report server can be started with python -m http.server 8788 "
                    "--bind 127.0.0.1 --directory reports.\n")
with zipfile.ZipFile(destination) as archive:
    assert archive.testzip() is None
digest = hashlib.sha256(destination.read_bytes()).hexdigest()
(root / "dist/SHA256SUMS-technical").write_text(f"{digest}  {destination.name}\n")
print(json.dumps({"path": str(destination), "files": count, "bytes": destination.stat().st_size,
                  "sha256": digest}, indent=2))
