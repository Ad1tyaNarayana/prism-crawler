# Usage

Install with `bash scripts/setup.sh`. The default adaptive route starts with HTTP, follows advertised structured alternatives within authorized scope, and uses at most one guarded browser attempt when content checks require it. The CLI exports failures alongside successes, with status, reason, timing and verification metadata.

## Budgets and scope

`prism crawl --help` lists all limits. Important controls are `--max-pages`, `--max-requests`, `--max-depth`, `--run-timeout`, `--timeout`, `--browser-timeout`, and `--max-bytes`. Documents serialize per origin; `--workers` permits progress across origins. Browser assets have a separate bounded concurrency lane.

Each seed authorizes its origin. `--redirect-origin` permits a public redirect helper without adding its pages to the frontier. `--asset-origin` permits browser GET dependencies. Both enforce robots and network scope. `--exclude-origin` explicitly blocks an origin. Local fixtures require `--allow-private`; it is off by default.

Example of a previously tested browser dependency profile:

```sh
.venv/bin/prism crawl https://www.windy.com/ --max-pages 1 \
  --no-discover --no-follow-links --db data/windy.db \
  --asset-origin https://node.windy.com \
  --asset-origin https://account.windy.com \
  --asset-origin https://ims.windy.com
```

This extracts text and accessible forecast data, not a full visual weather map. The short demo uses deterministic local fixtures so its availability does not depend on these public services.

## Resume and freshness

Repeat an interrupted command with `--resume`, preserving its seeds and configuration. SQLite requeues abandoned leases. Page results and discovered children commit together. Recovery is at-least-once for network requests, with one stored result per URL and run.

`prism recrawl --db data/example.db` revisits due pages. Add `--loop --tick 60` for a foreground loop. Successful pages save validators. Unchanged content extends the interval by 1.6; changed content halves it, within configured bounds. Rendered pages are revisited because an unchanged shell does not prove unchanged rendered content. This is a heuristic, not a trained freshness predictor.

## Optional decisions

`bash scripts/setup-models.sh` installs optional model environments. `bash scripts/serve-kev.sh` starts the local KEV server. Models are unnecessary for the demo, benchmarks and default crawler.

```sh
.venv/bin/prism crawl https://example.com --db data/shadow.db \
  --decision-provider systemone --decision-endpoint http://127.0.0.1:8019 \
  --decision-model kev-latest --decision-mode shadow
```

Shadow mode records advice without changing routing or parsing. Assist mode selects only existing source-derived parser candidates above its confidence threshold; it cannot override policy. System One is a typed decision API, not an OpenAI chat-completions endpoint. Do not install the unrelated PyPI package named `kev`; use the pinned setup script.
