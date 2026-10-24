"""Summarize frozen OSS trials without embedding third-party page bodies."""

import argparse
import html
import json
import shutil
import statistics
from collections import Counter
from pathlib import Path

PROVIDERS = ("prism", "crawlee", "crawl4ai")
QUALITY = ("source_token_precision", "source_token_recall", "source_token_f1",
           "weighted_block_recall", "heading_recall", "code_block_recall")


def median(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def mean(values):
    values = [v for v in values if v is not None]
    return statistics.mean(values) if values else None


def summarize(root, shared_exclusions=()):
    data = json.loads((root / "summary.json").read_text())
    rows = data["observations"]
    manifest = {c["id"]: c for c in json.loads(Path("benchmarks/oss50.json").read_text())}
    identifiers = {r["id"] for r in rows}
    excluded = (set(data["shared_cloudflare_exclusions"]) | set(shared_exclusions)) & identifiers
    cases = []
    for ident in dict.fromkeys(r["id"] for r in rows):
        selected = [r for r in rows if r["id"] == ident]
        entry = {"id": ident, "category": selected[0]["category"],
                 "url": manifest.get(ident, {}).get("url"),
                 "cohort": selected[0]["cohort"], "cloudflare_excluded": ident in excluded,
                 "reference_eligibility": selected[0]["reference_eligibility"], "providers": {}}
        for provider in PROVIDERS:
            group = [r for r in selected if r["provider"] == provider]
            if not group:
                continue
            quality = [r["quality"] for r in group if r["quality"] is not None]
            entry["providers"][provider] = {
                "observations": len(group), "outcomes": dict(Counter(r["outcome"] for r in group)),
                "strict_passes": sum(q["quality_pass"] for q in quality),
                "scored_trials": len(quality) if ident not in excluded else 0,
                "quality": {k: median(q[k] for q in quality) for k in QUALITY},
                "median_end_to_end_s": median(r["end_to_end_s"] for r in group),
                "min_end_to_end_s": min((r["end_to_end_s"] for r in group if r["end_to_end_s"] is not None), default=None),
                "max_end_to_end_s": max((r["end_to_end_s"] for r in group if r["end_to_end_s"] is not None), default=None),
                "median_library_s": median(r.get("library_elapsed_s") for r in group),
                "mean_browser_pages": mean(r.get("browser_pages") for r in group),
                "browser_trials": sum((r.get("browser_pages") or 0) > 0 for r in group),
                "median_requests": median(r.get("request_count") for r in group),
                "median_peak_rss_mib": median((r.get("peak_sampled_rss_bytes") or 0) / 2**20 for r in group),
            }
        cases.append(entry)
    eligible = [c for c in cases if c["reference_eligibility"] == "eligible" and not c["cloudflare_excluded"]]
    paired_pass = [c["id"] for c in eligible if len(c["providers"]) == 3 and all(
        p["strict_passes"] == p["observations"] for p in c["providers"].values())]
    aggregates = {}
    for provider in PROVIDERS:
        values = [c["providers"][provider] for c in eligible if provider in c["providers"]]
        retrieval = [r for r in rows if r["provider"] == provider and r["id"] not in excluded]
        aggregates[provider] = {
            "non_cloudflare_trials": len(retrieval),
            "non_cloudflare_outcomes": dict(Counter(r["outcome"] for r in retrieval)),
            "non_cloudflare_nonempty_trials": sum(r["outcome"] == "ok" and r["chars"] > 0 for r in retrieval),
            "scored_sites": len(values), "scored_trials": sum(v["scored_trials"] for v in values),
            "strict_passes": sum(v["strict_passes"] for v in values),
            "all_repetitions_pass_sites": sum(v["strict_passes"] == v["observations"] for v in values),
            "macro_site_median_quality": {k: mean(v["quality"][k] for v in values) for k in QUALITY},
            "median_site_median_s": median(v["median_end_to_end_s"] for v in values),
            "max_site_median_s": max((v["median_end_to_end_s"] for v in values if v["median_end_to_end_s"] is not None), default=None),
            "browser_trials": sum(v["browser_trials"] for v in values),
            "mean_browser_pages": mean(v["mean_browser_pages"] for v in values),
            "median_site_requests": median(v["median_requests"] for v in values),
            "median_peak_rss_mib": median(v["median_peak_rss_mib"] for v in values),
            "paired_pass_median_s": median(c["providers"][provider]["median_end_to_end_s"] for c in eligible if c["id"] in paired_pass),
        }
    notes = {
        "final-verified": "One live interleaved pass after tuning. This is final validation, not another unseen benchmark.",
        "reserved-final": "Twenty reserved URLs, first provider exposures after initial fixes, three repetitions per provider. Later fixes are reported separately.",
    }
    return {"phase": root.name, "note": data.get("meaning", notes.get(root.name, "Live retrieval measurements")),
            "finished_at": data["finished_at"], "total_sites": len(cases),
            "quality_eligible_sites": len(eligible), "cloudflare_exclusions": sorted(excluded),
            "reference_counts": dict(Counter(c["reference_eligibility"] for c in cases)),
            "paired_all_pass_sites": paired_pass, "providers": aggregates, "cases": cases}


def number(value, percent=False):
    if value is None:
        return "—"
    return f"{value * 100:.1f}%" if percent else f"{value:.2f}"


def render(phases):
    sections = []
    for phase in phases:
        header = (f"<h2>{html.escape(phase['phase'])}</h2><p>{phase['total_sites']} URLs; "
                  f"{phase['quality_eligible_sites']} quality-eligible after shared Cloudflare exclusions. "
                  f"Excluded: {html.escape(', '.join(phase['cloudflare_exclusions']) or 'none')}.</p>")
        header += f"<p><small>{html.escape(phase['note'])}</small></p>"
        rows = []
        for provider, p in phase["providers"].items():
            if not p["scored_trials"] and not p["non_cloudflare_trials"]:
                continue
            q = p["macro_site_median_quality"]
            rows.append(f"<tr><th>{provider}</th><td>{number(q['source_token_f1'], True)}</td>"
                        f"<td>{number(q['weighted_block_recall'], True)}</td>"
                        f"<td>{p['strict_passes']}/{p['scored_trials']}</td>"
                        f"<td>{number(p['median_site_median_s'])}</td>"
                        f"<td>{p['browser_trials']}/{p['scored_trials']}</td>"
                        f"<td>{number(p['median_site_requests'])}</td></tr>")
        table = ("<div class='scroll'><table><thead><tr><th>Library</th><th>Token F1</th>"
                 "<th>Blocks recovered</th><th>Strict pass trials</th><th>Median seconds</th>"
                 "<th>Browser trials</th><th>Median requests</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>")
        retrieval = "<p>All non-Cloudflare trials, including unscorable references: " + "; ".join(
            f"{provider}: {p['non_cloudflare_nonempty_trials']}/{p['non_cloudflare_trials']} nonempty OK responses"
            for provider, p in phase["providers"].items() if p["non_cloudflare_trials"]) + ". This is retrieval status, not a content-quality pass.</p>"
        details = []
        for c in phase["cases"]:
            label = "Cloudflare excluded" if c["cloudflare_excluded"] else c["reference_eligibility"]
            title = html.escape(c["id"])
            if c["url"]:
                title = f"<a href='{html.escape(c['url'], quote=True)}'>{title}</a>"
            row = f"<tr><th>{title}</th><td>{html.escape(label)}</td>"
            for provider in PROVIDERS:
                p = c["providers"].get(provider)
                if p is None:
                    row += "<td>—</td>"
                    continue
                f1 = p["quality"]["source_token_f1"] if p["scored_trials"] else None
                outcomes = html.escape(', '.join(f'{k}: {v}' for k, v in p['outcomes'].items()))
                row += (f"<td>{number(f1, True)} · {number(p['median_end_to_end_s'])}s · "
                        f"{number(p['mean_browser_pages'])} browser<br><small>{outcomes}; "
                        f"strict {p['strict_passes']}/{p['scored_trials']}</small></td>")
            details.append(row + "</tr>")
        sections.append(header + table + retrieval + "<details><summary>Every URL: F1 · seconds · browser pages</summary>"
                        "<div class='scroll'><table><thead><tr><th>Site</th><th>Eligibility</th>"
                        "<th>Prism</th><th>Crawlee</th><th>Crawl4AI</th></tr></thead><tbody>" + "".join(details) + "</tbody></table></div></details>")
    return """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Prism · local OSS crawler comparison</title><style>
body{font:16px/1.55 system-ui,sans-serif;max-width:1180px;margin:48px auto;padding:0 24px;background:#f5f4ef;color:#1b302a}h1{font-size:36px;letter-spacing:-1px}h2{margin-top:48px}table{border-collapse:collapse;width:100%;background:white;font-variant-numeric:tabular-nums}th,td{padding:12px 15px;border-bottom:1px solid #dce1db;text-align:left;vertical-align:top}thead{background:#e6ece5}small{color:#61736a}summary{cursor:pointer;margin:18px 0}.scroll{overflow:auto}a{color:#176957}.note{border-left:4px solid #759b86;padding:8px 20px;background:#eaf0e8}p{max-width:920px}</style>
<h1>Prism vs Crawlee vs Crawl4AI</h1><p>Local, live retrieval on a frozen set of fresh public websites. Free software, fresh state per trial, common honest identity and browser profile, robots enforced, confirmed Cloudflare challenges excluded for all three.</p>
<div class="note">Holdout source labels were reviewed and locked before provider outputs. Development label corrections are separately marked post-hoc analyses. These are model-assisted region annotations, not an independent human evaluation. Read strict pass rates alongside the content audit: references can omit useful leaf text or include interface text. Token F1 does not verify code syntax or table structure.</div>
<p>Numbers first aggregate repetitions by site median, then average quality across sites or take the median latency across sites. Timing includes startup, policy checks and failures; it is not a speed comparison at equal quality. Browser usage is observed page creation, not a claim about warm-session throughput. Crawlee is tested as a cold adaptive retriever with a fixed generic Trafilatura handler; Crawl4AI uses fixed pruning with raw Markdown fallback when pruning is empty. Development results are separated from untouched final holdout results.</p>
""" + "".join(sections) + "<p><a href='analysis.html'>Failures, fixes and interpretation</a> · <a href='method.html'>Method and reproduction</a> · <a href='summary.json'>Numerical results</a>. Source bodies remain local and are not included in this report.</p></html>"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phases", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    shared = set().union(*(set(json.loads((p / "summary.json").read_text())["shared_cloudflare_exclusions"])
                           for p in args.phases))
    phases = [summarize(p, shared) for p in args.phases]
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "summary.json").write_text(json.dumps(phases, indent=2))
    (args.output / "index.html").write_text(render(phases))
    from markdown_it import MarkdownIt

    for target, name in [("analysis", "oss-benchmark-results"), ("method", "oss-benchmark-method")]:
        source = Path("docs") / (name + ".md")
        content = MarkdownIt("commonmark", {"html": False}).enable("table").render(source.read_text())
        for old, new in {
            "oss-benchmark-method.md": "method.html",
            "../examples/oss-evidence/summary.json": "summary.json",
            "../examples/oss-evidence/phase-fingerprints.json": "phase-fingerprints.json",
            "../examples/oss-evidence/raw-holdout-audit.json": "raw-holdout-audit.json",
            "../examples/oss-evidence/raw-final-audit.json": "raw-final-audit.json",
        }.items():
            content = content.replace(f'href="{old}"', f'href="{new}"')
        (args.output / (target + ".html")).write_text(
            '<!doctype html><html lang="en"><meta charset="utf-8"><title>Prism comparison</title>'
            '<style>body{font:16px/1.65 system-ui;max-width:960px;margin:48px auto;padding:0 24px;'
            'color:#1b302a;background:#f5f4ef}table{border-collapse:collapse;background:white}th,td{'
            'padding:10px;border:1px solid #ccd8cc}pre{overflow:auto}a{color:#176957}</style>'
            '<a href="index.html">← Comparison report</a>' + content + '</html>')
    for name in ["phase-fingerprints.json", "raw-holdout-audit.json", "raw-final-audit.json"]:
        source = Path("examples/oss-evidence") / name
        if source.exists():
            shutil.copy2(source, args.output / name)
    for p in phases:
        print(p["phase"], p["quality_eligible_sites"], json.dumps(p["providers"], indent=2))


if __name__ == "__main__":
    main()
