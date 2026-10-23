#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/prism crawl https://www.nature.com/nature/about https://www.windy.com/ \
  --redirect-origin https://idp.nature.com \
  --asset-origin https://node.windy.com \
  --asset-origin https://account.windy.com \
  --asset-origin https://ims.windy.com \
  --max-pages 2 --no-discover --no-follow-links --db data/adaptive-demo.db
.venv/bin/prism inspect --db data/adaptive-demo.db
.venv/bin/prism export --db data/adaptive-demo.db --output data/adaptive-demo.jsonl
