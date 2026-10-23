#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec .runtime/kev-env/bin/python -m kev.serve \
  --run jaredpalmer/kev-0.8b@v1.0 --host 127.0.0.1 --port 8019
