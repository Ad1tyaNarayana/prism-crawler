#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/prism crawl https://quotes.toscrape.com/js/ \
  --strategy adaptive --max-pages 1 --no-discover --no-follow-links --db data/demo.db
.venv/bin/prism inspect --db data/demo.db
.venv/bin/prism export --db data/demo.db --output data/demo.jsonl
.venv/bin/python -c 'import json; from pathlib import Path; print(json.loads(Path("data/demo.jsonl").read_text())["text"][:1000])'
