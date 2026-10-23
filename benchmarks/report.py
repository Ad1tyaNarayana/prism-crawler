import html
import json
import statistics
from pathlib import Path

ROOT = Path("reports")
NAMES = {"http": "Lean HTTP", "browser": "Render every HTML page", "adaptive": "Evidence router"}


def read(path):
    return json.loads((ROOT / path).read_text())


def escape(value):
    return html.escape(str(value), quote=True)


def main():
    benchmark = read("benchmark/summary.json")
    models = read("models/summary.json")
    blocks = read("blocks/summary.json")
    english = read("english/summary.json")
    models["models"]["laya-english"] = english["broad"]
    blocks["models"]["laya-english"] = english["blocks"]
    live = read("live/summary.json")
    evidence = {strategy: read(f"benchmark/run-0-{strategy}.json") for strategy in NAMES}
    adaptive = benchmark["results"]["adaptive"]
    browser = benchmark["results"]["browser"]
    speed = browser["median_ms"] / adaptive["median_ms"]
    fewer_browsers = 100 * (1 - statistics.median(adaptive["browser_pages"]) / statistics.median(browser["browser_pages"]))
    strategy_rows = []
    for strategy, stats in benchmark["results"].items():
        passed = min(stats["content_passed"])
        total = stats["content_total"]
        strategy_rows.append(f"""<tr class="{'winner' if strategy == 'adaptive' else ''}">
          <th scope="row">{NAMES[strategy]}{' <span class="badge">selected</span>' if strategy == 'adaptive' else ''}</th>
          <td><b>{passed}/{total}</b><div class="bar"><i style="width:{100 * passed / total}%"></i></div></td>
          <td>{stats['median_ms'] / 1000:.2f} s<small>{stats['min_ms'] / 1000:.2f}–{stats['max_ms'] / 1000:.2f} s</small></td>
          <td>{statistics.median(stats['requests']):.0f}</td><td>{statistics.median(stats['browser_pages']):.0f}</td>
          <td>{min(stats['policy_passed'])}/{stats['policy_total']}</td></tr>""")
    case_rows = []
    paths = [case["path"] for case in evidence["adaptive"]["score"]["cases"]]
    for path in paths:
        cases = {s: next(c for c in evidence[s]["score"]["cases"] if c["path"] == path) for s in NAMES}
        failure = any(not c["passed"] for c in cases.values())
        cells = []
        for strategy, case in cases.items():
            document = next(d for d in evidence[strategy]["documents"] if d["url"].endswith(path))
            label = "Pass" if case["passed"] else "Miss"
            if case["kind"] == "policy" and case["passed"]:
                label = "Correct refusal"
            cells.append(f"""<td><details><summary><span class="status {'pass' if case['passed'] else 'fail'}">{label}</span>
                <small>{escape(case['method'])} · {escape(case['outcome'])}</small></summary>
                <p class="excerpt">{escape(document['text'][:700] or case['reason'])}</p>
                <p class="meta">Parser {escape(document['parser'] or 'none')} · {case['chars']} characters</p>
                </details></td>""")
        case_rows.append(f'<tr data-failed="{str(failure).lower()}"><th scope="row">{escape(path)}</th>{"".join(cells)}</tr>')
    model_rows = []
    for name, metrics in models["models"].items():
        narrow = blocks["models"][name]
        model_rows.append(f"""<tr><th scope="row">{escape(name)}</th>
            <td>{round(metrics['accuracy'] * 24)}/24<small>routing + pairwise parsing</small></td>
            <td>{round(narrow['accuracy'] * 24)}/24<small>content classification</small></td>
            <td>{metrics['median_ms']:.1f} ms<small>warm, broad task</small></td>
            <td>{metrics['accepted_errors'] // 5}/{metrics['accepted_at_0_9'] // 5}<small>wrong / accepted at ≥0.9</small></td></tr>""")
    live_rows = []
    for record in live["runs"]:
        live_rows.append(f"<tr><th>{NAMES[record['strategy']]}</th><td>{record.get('content_checks_passed','?')}/6</td>"
                         f"<td>{record['requests']}</td><td>{record['browser_pages']}</td>"
                         f"<td><a href='live/{record['strategy']}.json'>Raw evidence ↗</a></td></tr>")
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Prism · Crawler experiments</title><style>
:root{{--paper:#f3f0e8;--ink:#192622;--muted:#5f6d67;--line:#cdd2c9;--accent:#df5c31;--green:#21694c}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font-family:'Avenir Next','Helvetica Neue',sans-serif;line-height:1.55}}
a{{color:inherit;text-underline-offset:4px}}a:hover{{color:var(--accent)}}header,main,footer{{max-width:1180px;margin:auto;padding:0 36px}}
header{{padding-top:28px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--line);padding-bottom:20px}}
.brand{{letter-spacing:5px;font-size:19px;font-weight:800}}.brand:before{{content:'◩';color:var(--accent);margin-right:12px}}nav{{display:flex;gap:24px;font-size:13px}}
.hero{{display:grid;grid-template-columns:1.65fr 1fr;gap:48px;padding:70px 0 50px;border-bottom:1px solid var(--line)}}
.eyebrow{{font:12px 'SFMono-Regular',Consolas,monospace;letter-spacing:2px;text-transform:uppercase;color:var(--muted)}}
h1{{font-family:Georgia,serif;font-size:clamp(46px,5.8vw,78px);line-height:1.04;font-weight:400;letter-spacing:-3px;margin:24px 0}}
h1 em{{font-style:normal;color:var(--accent)}}.lede{{font-size:17px;max-width:590px;color:var(--muted)}}
.verdict{{padding:28px;border-left:2px solid var(--accent);align-self:end}}.big{{font:70px Georgia,serif;letter-spacing:-3px;line-height:1.1;margin:12px 0}}.verdict p{{font-size:14px;color:var(--muted)}}
section{{padding:44px 0;border-bottom:1px solid var(--line);scroll-margin-top:24px}}h2{{font-family:Georgia,serif;font-size:33px;line-height:1.2;font-weight:400;margin:12px 0 20px}}
.section-title{{display:flex;align-items:baseline;justify-content:space-between;gap:20px}}.note{{max-width:880px;font-size:14px;color:var(--muted)}}
.table-wrap{{overflow-x:auto}}table{{width:100%;border-collapse:collapse;text-align:left;font-size:14px}}th,td{{padding:19px 14px;border-bottom:1px solid var(--line);vertical-align:top}}th:first-child,td:first-child{{padding-left:0}}
thead th{{font-size:11px;text-transform:uppercase;letter-spacing:1px;color:var(--muted);font-weight:600}}tbody th{{font-weight:600;max-width:245px}}small{{display:block;font-size:11px;color:var(--muted);margin-top:5px}}
.winner{{background:#e5ebdf}}.winner th{{padding-left:12px}}.badge{{display:inline-block;padding:3px 7px;background:var(--green);color:white;font-size:9px;text-transform:uppercase;letter-spacing:1px;margin-left:4px}}
.bar{{height:4px;width:100px;background:#d5d8cf;margin-top:8px}}.bar i{{display:block;height:100%;background:var(--green)}}
.tiles{{display:grid;grid-template-columns:repeat(3,1fr);gap:30px;margin-top:30px}}.tile{{border-top:2px solid var(--ink);padding-top:17px}}.tile h3{{font:22px Georgia,serif;margin:0 0 12px}}.tile p{{font-size:14px;color:var(--muted)}}
.number{{font:12px 'SFMono-Regular',monospace;color:var(--accent)}}.filters{{display:flex;gap:12px;flex-wrap:wrap;margin:20px 0}}input,button{{font:inherit;background:transparent;border:1px solid #9aa79d;padding:10px 14px;border-radius:0}}input{{min-width:250px}}button{{cursor:pointer}}button.active{{background:var(--ink);color:var(--paper)}}button:focus-visible,input:focus-visible,summary:focus-visible{{outline:3px solid var(--accent);outline-offset:3px}}
summary{{cursor:pointer;list-style:none}}summary:after{{content:' +';color:var(--muted)}}details[open] summary:after{{content:' −'}}.status{{font-weight:600}}.pass{{color:var(--green)}}.fail{{color:#ae3820}}
.excerpt{{max-width:260px;max-height:200px;overflow:auto;font-size:12px;white-space:pre-line;line-height:1.6}}.meta{{font-size:10px;color:var(--muted)}}
.callout{{border-left:3px solid var(--accent);padding:14px 24px;margin:26px 0;background:#ebe6dc}}.callout p{{margin:0;font-size:14px}}
code,pre{{font-family:'SFMono-Regular',Consolas,monospace}}code{{overflow-wrap:anywhere}}pre{{white-space:pre-wrap;background:var(--ink);color:#e6eee5;padding:24px;line-height:1.8;font-size:13px;overflow:auto}}
.sources{{display:grid;grid-template-columns:1fr 1fr;gap:12px 40px;font-size:14px}}footer{{padding-top:30px;padding-bottom:40px;font-size:12px;color:var(--muted)}}[hidden]{{display:none!important}}
@media(max-width:760px){{header,main,footer{{padding-left:20px;padding-right:20px}}nav{{gap:12px}}nav a:last-child{{display:none}}.hero{{grid-template-columns:1fr;gap:10px;padding:40px 0}}h1{{letter-spacing:-2px}}.verdict{{padding:16px 24px}}.big{{font-size:52px}}.tiles,.sources{{grid-template-columns:1fr}}.section-title{{display:block}}table{{min-width:650px}}}}
</style></head><body>
<header><div class="brand">PRISM</div><nav><a href="#results">Strategies</a><a href="#models">Models</a><a href="#cases">Evidence</a><a href="#run">Run it</a></nav></header>
<main><div class="hero"><div><div class="eyebrow">Crawler lab / Project 02 / 23 October 2026</div><h1>Crawl cheaply.<br><em>Show the evidence.</em></h1>
<p class="lede">A working, local crawler with three competing strategies, a persistent frontier, and optional Laya or KEV decisions. Every result below comes from a saved run.</p>
<p class="eyebrow">22 fixture cases · 5 interleaved runs per strategy</p></div>
<aside class="verdict"><div class="eyebrow">Selected on this corpus</div><div class="big">{min(adaptive['content_passed'])}/{adaptive['content_total']}</div><b>Evidence router</b>
<p>{speed:.1f}× faster than rendering every HTML page in this local test. {fewer_browsers:.0f}% fewer browser pages. Four policy checks passed on every run.</p></aside></div>
<section id="results"><div class="section-title"><div><div class="eyebrow">01 / Controlled comparison</div><h2>Pay for rendering when it helps.</h2></div><a href="benchmark/summary.json">Measurements ↗</a></div>
<div class="table-wrap"><table><thead><tr><th>Approach</th><th>Content checks</th><th>Median / range</th><th>Requests</th><th>Browser pages</th><th>Policy checks</th></tr></thead><tbody>{''.join(strategy_rows)}</tbody></table></div>
<p class="note">All three share the same parser, scope, robots rules, request budget and pacing. Times include browser startup and run work. This is a local fixture benchmark with a 5 ms minimum delay. It does not predict internet throughput or prove universal optimality. Exact request totals match independent origin-server receipts.</p>
<div class="tiles"><div class="tile"><span class="number">01</span><h3>Lean HTTP</h3><p>One HTTP representation, including JSON-LD and hydration data. Cheapest on static pages. It misses client-rendered content and the separate advertised API response.</p></div>
<div class="tile"><span class="number">02</span><h3>Render every HTML page</h3><p>A shared Chromium process renders HTML. Native JSON and feeds bypass rendering. A plain HTTP fallback recovers browser failures. It recovers more pages at a higher runtime cost.</p></div>
<div class="tile"><span class="number">03</span><h3>Evidence router</h3><p>Try source-backed structured data and advertised JSON before rendering. Learn when a URL template needs a browser. Periodically recheck the cheaper route.</p></div></div></section>
<section id="models"><div class="section-title"><div><div class="eyebrow">02 / Local decision models</div><h2>Typed output does not guarantee a good decision.</h2></div><a href="models/summary.json">Model results ↗</a></div>
<div class="table-wrap"><table><thead><tr><th>Model</th><th>Broad decisions</th><th>Narrow classification</th><th>Latency</th><th>Confident mistakes</th></tr></thead><tbody>{''.join(model_rows)}</tbody></table></div>
<p class="note">Each accuracy uses 24 distinct development cases, repeated five times. Repetition checks consistency; it does not create 120 independent examples. Laya runs through PyTorch MPS and KEV-0.8B through a local MLX server. Warm latency includes each adapter and may benefit from runtime caching. Jev's adapter is implemented but its hosted model was not called.</p>
<div class="callout"><p><b>Default: deterministic routing.</b> KEV is an optional adviser for classifying parser candidates. On a full-page ambiguity test, its ≥0.9 gate abstained. A deterministic repetition check and comparison of all semantic containers fixed that case. There is no measured end-to-end quality gain from enabling a model on the final fixture corpus.</p></div>
<p class="note">The model cannot invent extracted text, expand origin scope, override robots, solve access challenges, or send a website POST request. Hosted endpoints receive the selected page snippets only when explicitly configured.</p></section>
<section id="cases"><div class="eyebrow">03 / Inspect the actual outputs</div><h2>What worked. What failed.</h2>
<div class="filters"><input id="search" type="search" aria-label="Filter fixture cases" placeholder="Filter cases, e.g. /js or /private"><button class="active" data-filter="all">All cases</button><button data-filter="failed">Strategy misses</button><span id="visible-count" class="note" aria-live="polite"></span></div>
<div class="table-wrap"><table id="case-table"><thead><tr><th>Fixture</th><th>Lean HTTP</th><th>Browser first</th><th>Evidence router</th></tr></thead><tbody>{''.join(case_rows)}</tbody></table></div>
<p class="note">Open a cell to inspect its text or refusal reason. A policy pass means the crawler correctly refused the request; it is not counted as recovered content. These are synthetic fixtures built for reproducibility.</p></section>
<section id="live"><div class="eyebrow">04 / Real websites</div><h2>A separate live smoke test.</h2><div class="table-wrap"><table><thead><tr><th>Approach</th><th>Content markers</th><th>Requests</th><th>Browser pages</th><th>Evidence</th></tr></thead><tbody>{''.join(live_rows)}</tbody></table></div>
<p class="note">Six URLs across five origins: Quotes to Scrape, Books to Scrape, Python documentation, httpbin and Scrape This Site. This single-run check verifies expected content markers, including both ends of the country directory. It is not a completeness benchmark. Live durations are intentionally excluded from the strategy speed claim.</p></section>
<section id="run"><div class="eyebrow">05 / Use the selected version</div><h2>Run it locally. Keep the evidence.</h2><pre>cd /Users/aditya/Me/optimal-crawler
.venv/bin/prism crawl https://quotes.toscrape.com/js/ \\
  --strategy adaptive --max-pages 5 --db data/my-crawl.db
.venv/bin/prism inspect --db data/my-crawl.db
.venv/bin/prism export --db data/my-crawl.db --output data/pages.jsonl</pre>
<p class="note">Source, setup instructions and the full limitations are in the project README. Add <code>--decision-provider systemone --decision-mode shadow</code> to record KEV advice from the local server on port 8019. The base crawler needs no model key.</p>
<div class="callout"><p><b>Scope.</b> This is a tested single-machine research system for public HTML, JSON and XML. Authentication, CAPTCHA solving, paywalls, arbitrary form submission, PDF extraction, cross-origin application dependencies and infinite-scroll completion are outside the verified scope. A distributed production service and an independent security review remain future work.</p></div></section>
<section><div class="eyebrow">06 / Prior work and reproducibility</div><h2>Built on existing techniques.</h2><div class="sources">
<a href="https://docs.scrapy.org/en/latest/topics/autothrottle.html">Scrapy · adaptive pacing</a><a href="https://crawlee.dev/python/docs/guides/adaptive-playwright-crawler">Crawlee · learned rendering selection</a>
<a href="https://docs.crawl4ai.com/core/adaptive-crawling/">Crawl4AI · information-gain traversal</a><a href="https://github.com/jaredpalmer/kev">KEV · open System One models</a>
<a href="https://huggingface.co/convaiinnovations/laya">Laya · model card and limitations</a><a href="https://docs.typesafe.ai/api/system-one">Jev · System One API</a>
<a href="benchmark/summary.json">Five-run crawler summary</a><a href="blocks/summary.json">Narrow classifier evaluation</a>
<a href="ablation/summary.json">End-to-end model ablation</a><a href="pre-quality-ablation/summary.json">Preserved failed ambiguity experiment</a></div>
<p class="note">Fixture contract SHA-256: <code>{benchmark['contract_sha256']}</code>.<br>Measured runtime source SHA-256: <code>{benchmark['source_sha256']}</code>.<br>Hardware: Apple M4 Pro, 12 logical CPUs, 24 GiB memory. Python {benchmark['python']}. All measurements are local; electricity and amortized hardware costs were not measured. No hosted inference was purchased.</p></section></main>
<footer>PRISM / A reviewable research build. Source, raw JSON, SQLite checkpoints, tests and decision trail live alongside this report.</footer>
<script>
const rows = Array.from(document.querySelectorAll('#case-table tbody tr'));
const search = document.getElementById('search'); let mode = 'all';
function filter() {{let visible=0; const q=search.value.toLowerCase();rows.forEach(row=>{{const show=(!q||row.querySelector('th').textContent.toLowerCase().includes(q))&&(mode==='all'||row.dataset.failed==='true');row.hidden=!show;if(show)visible++}});document.getElementById('visible-count').textContent=visible+' cases';}}
search.addEventListener('input',filter);document.querySelectorAll('[data-filter]').forEach(button=>button.addEventListener('click',()=>{{mode=button.dataset.filter;document.querySelectorAll('[data-filter]').forEach(b=>b.classList.toggle('active',b===button));filter();}}));filter();
</script></body></html>"""
    (ROOT / "index.html").write_text(document)
    print(ROOT / "index.html")


if __name__ == "__main__":
    main()
