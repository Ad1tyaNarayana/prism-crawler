#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/python -m pip install 'laya==0.3.20'
mkdir -p .runtime
if [[ ! -x .runtime/kev-env/bin/python ]]; then
  python3 -m venv .runtime/kev-env
fi
.runtime/kev-env/bin/python -m pip install 'kev[serve] @ git+https://github.com/jaredpalmer/kev.git@1f04cfa28e6a708485b28a9585010b9e436b776e'
