# Prism

An adaptive, resumable web crawler built in Python. HTTP is the default; guarded Chromium handles pages that need JavaScript. Extraction, source checks, rate limits, robots policy and SQLite checkpoints run locally. No hosted crawler or model is required.

This is a single-machine backend engineering project, with reproducible failure experiments. It does not claim universal website coverage.

## Results and demo

- [Evaluation and limitations](docs/evaluation.md): 50 new hostnames, frozen before fetching, with a reserved ten-site cohort. Original failures and Cloudflare exclusions remain visible.
- [Interactive results and short recorded demo](examples/evidence/index.html): open through the local server below.
- [Architecture](docs/architecture.md) and [implementation limits](docs/limits.md).

The most consequential fix came from deliberately killing the process just after a page was saved. The old implementation lost 100 discovered children; the corrected implementation commits the document and its frontier additions in one SQLite transaction. The same experiment recovered **808/808**, versus **708/808** before the fix. Network requests can repeat after a crash; stored URL records remain unique.

Source review also caught two failures hidden by keyword checks: stripped card headings and CMS layout settings mixed with prose. Both have regression tests and fresh focused rechecks. A redirect-loop detector avoids repeating the same failed request six times.

## Run locally

Python 3.11+ on macOS or Linux; tested on Python 3.13, Apple M4 Pro. Setup downloads Python dependencies and Chromium. The default runtime needs no account, key or paid service.

```sh
bash scripts/setup.sh
.venv/bin/prism crawl https://example.com --max-pages 10 --db data/example.db
.venv/bin/prism inspect --db data/example.db
.venv/bin/prism export --db data/example.db --output data/example.jsonl
```

Resume an interrupted run by repeating its exact command with `--resume`. For an explicitly approved public redirect helper, add `--redirect-origin https://helper.example.org`; its robots rules still apply. Browser dependencies use `--asset-origin`. Neither option grants frontier scope on the helper. See `prism crawl --help` and [usage](docs/usage.md).

## Reproduce the engineering demo

```sh
# Entirely local: HTTP, JavaScript, temporary errors and two actual SIGKILL/resume trials.
PYTHONPATH=.:src .venv/bin/python -m benchmarks.demo --output reports/my-demo
# Serve the saved portfolio (open http://127.0.0.1:8789/examples/evidence/).
.venv/bin/python -m http.server 8789 --bind 127.0.0.1
```

The demo records Chromium video and saves SQLite databases, origin request receipts and correctness checks. Use a new output directory for each experiment.

## Verify

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check src tests benchmarks
PYTHONPATH=.:src .venv/bin/python -m benchmarks.load_recovery suite \
  --output reports/load-check --origins 8 --pages-per-origin 100 \
  --workers 1 4 8 --recovery --mixed
PYTHONPATH=.:src .venv/bin/python -m benchmarks.load_recovery suite \
  --output reports/sustained-check --origins 8 --pages-per-origin 1000 \
  --workers 8 --fixture-delay .06
```

The core tests and load fixtures use local servers. The frozen web benchmark does make real requests:

```sh
PYTHONPATH=.:src .venv/bin/python -m benchmarks.holdout \
  --cohort initial --output reports/new-initial
PYTHONPATH=.:src .venv/bin/python -m benchmarks.holdout \
  --cohort reserved --output reports/new-reserved
```

Rerunning a published cohort does not make it untouched again. Robots enforcement stays enabled; Cloudflare responses are reported as skipped, not solved or replaced.

## Repository map

`src/optimal_crawler/` contains the supported adaptive engine. `tests/` contains local behavioral tests. `benchmarks/` contains evaluation tools; older provider/model experiments are historical research, not the default path. `experiments/` holds isolated native-network and model-controller experiments, outside the production runtime. `examples/evidence/` contains small, content-free result receipts and the local demo. Raw website captures, provider account files, model weights and old reports remain ignored on the original machine.

Optional Laya/KEV adapters can advise bounded parser selection. The measured default is deterministic: models did not improve the earlier end-to-end fixture score. They cannot generate the saved document or override access policy. This project has no distributed frontier, multi-tenant API or demonstrated million-page capacity.
