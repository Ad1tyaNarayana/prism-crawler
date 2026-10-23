#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/prism crawl https://exa.ai/about https://exa.ai/pricing \
  --max-pages 2 --no-discover --no-follow-links --db data/recovery-demo.db
.venv/bin/prism inspect --db data/recovery-demo.db
.venv/bin/prism export --db data/recovery-demo.db --output data/recovery-demo.jsonl
