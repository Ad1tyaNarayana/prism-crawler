"""Itemize the original 17 and verify the final adaptive-only runs."""

import csv
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path

from markdown_it import MarkdownIt

from benchmarks.real50 import runtime_digest
from benchmarks.technical_report import functional_checks

ROOT = Path("reports/adaptive")
GROUPS = {
    "access_wall": [
        "britannica",
        "usgs",
        "smithsonian",
        "britishmuseum",
        "patagonia",
        "bookshop",
        "apnews",
        "khanacademy",
    ],
    "explicit_robots": ["reuters", "lobsters"],
    "unreadable_robots": ["noaa", "mayoclinic", "metmuseum"],
    "redirect_helper": ["nature"],
    "wrong_item": ["ikea"],
    "transport": ["nhk-ja"],
    "title_preservation": ["windy"],
}
EXPLANATIONS = {
    "britannica": "HTTP 403 verification wall.",
    "usgs": "HTTP 202 robot-verification page; running its challenge is excluded.",
    "smithsonian": "HTTP 403 request-verification wall.",
    "britishmuseum": "HTTP 403 verification wall.",
    "patagonia": "HTTP 200 queue/verification page instead of the footprint article.",
    "bookshop": "HTTP 403 blocked response.",
    "apnews": "HTTP 403 verification wall.",
    "khanacademy": "HTTP 200 Client Challenge instead of algebra content.",
    "reuters": "A parsed robots rule disallows this URL.",
    "lobsters": "A parsed robots rule disallows this URL.",
    "noaa": "Robots returns HTTP 403. Policy unreadable, not an explicit parsed disallow. Content is not requested.",
    "mayoclinic": "Robots returns HTTP 403. Policy unreadable, not an explicit parsed disallow. Content is not requested.",
    "metmuseum": "Robots returns HTTP 429. Bounded retries now respect Retry-After, but the live policy file remains unavailable.",
    "nature": "Recovered through an explicitly authorized public GET redirect helper, with its own robots check. No cookies or credentials used.",
    "ikea": "Original product redirects to a category; captured category source contains zero occurrences of item 00263850. It is not substituted.",
    "nhk-ja": "No usable response from this machine. HTTP/1.1 fails at 15 and a configured 45 seconds; independent HTTP/2 returns a stream INTERNAL_ERROR after verified TLS.",
    "windy": "Recovered by preserving the actual source title in extracted text. The unchanged body-marker check and separate live-forecast functional check both pass.",
}


def load(p):
    return json.loads(Path(p).read_text())


def main():
    before = load(ROOT / "before17/run-0.json")
    original = load("reports/continuation/comparison.json")
    assert {r["id"] for r in before["reviewed"]} == {r["id"] for r in original["remaining"]}
    runs = [load(ROOT / f"final50/run-{i}.json") for i in range(3)]
    digest = runtime_digest()
    assert all(
        r["runtime_sha256"] == digest
        and r["summary"]["status"] == "completed"
        and len(r["documents"]) == 50
        for r in runs
    )
    assert all(
        r["summary"]["decision_calls"] == 0 and r["summary"]["strategy"] == "adaptive" for r in runs
    )
    assert "90 passed" in Path(".audit/adaptive-suite-release.log").read_text()
    verified = set()
    for r in [before, *runs]:
        directory = ROOT / ("before17" if r is before else "final50")
        for c in r["captures"]:
            sha = c.get("body_sha256")
            if sha:
                p = directory / "bodies" / f"{sha}.gz"
                if p not in verified:
                    assert hashlib.sha256(gzip.decompress(p.read_bytes())).hexdigest() == sha
                    verified.add(p)
    rows = []
    for b in before["reviewed"]:
        checks = [next(c for c in r["reviewed"] if c["id"] == b["id"]) for r in runs]
        group = next(g for g, ids in GROUPS.items() if b["id"] in ids)
        rows.append(
            {
                "id": b["id"],
                "category": group,
                "url": b["url"],
                "before_outcome": b["outcome"],
                "before_passed": b["passed"],
                "passes": sum(c["passed"] for c in checks),
                "repetitions": 3,
                "after_outcomes": dict(Counter(c["outcome"] for c in checks)),
                "explanation": EXPLANATIONS[b["id"]],
            }
        )
    assert len(rows) == 17 and len({r["id"] for r in rows}) == 17
    functionals = [functional_checks(r) for r in runs]
    summary = {
        "strategy": "adaptive",
        "runtime_sha256": digest,
        "repetitions": 3,
        "before_reviewed_score": 33,
        "scores": [sum(c["passed"] for c in r["reviewed"]) for r in runs],
        "requests": [r["summary"]["requests"] for r in runs],
        "browser_pages": [r["summary"]["browser_pages"] for r in runs],
        "functional": functionals,
        "remaining17": rows,
        "body_files_verified": len(verified),
        "original_manifest_sha256": hashlib.sha256(
            Path("benchmarks/real50.json").read_bytes()
        ).hexdigest(),
        "profile": load(ROOT / "final50/profile.json"),
    }
    original_ids = {r["id"] for r in rows}
    additional = [
        {"run": index + 1, "id": c["id"], "outcome": c["outcome"], "reason": c["reason"]}
        for index, run in enumerate(runs)
        for c in run["reviewed"]
        if not c["passed"] and c["id"] not in original_ids
    ]
    summary["additional_failures"] = additional
    (ROOT / "comparison.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    with (ROOT / "remaining17.csv").open("w") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    recovered = [r["id"] for r in rows if r["passes"] == 3]
    lines = [
        "# Adaptive crawler: all 17 remaining failures investigated",
        "",
        "The product now has one supported path: HTTP and advertised data first, guarded Chromium when needed, with source verification and bounded recovery. Historical baseline modes remain hidden compatibility options. No paid service, proxy, model call, CAPTCHA solving or access-wall bypass was used.",
        "",
        f"The prior reviewed score was **33/50**. Three final adaptive runs on the **same 50 URLs and unchanged content/review checks** scored **{' / '.join(str(n) for n in summary['scores'])} out of 50**. Recovered targets: **{', '.join(recovered)}**. The denominator remains 50.",
        "",
        "The final profile explicitly authorizes https://idp.nature.com for public redirect hops and its robots check. It retains the earlier Windy asset origins. This configured permission is material: Nature is not recovered by automatically trusting a related hostname. Default scope remains strict.",
        "",
        "## Breakdown of the original 17",
        "",
        "| Category | Count | Targets |",
        "| --- | --- | --- |",
    ]
    for group, ids in GROUPS.items():
        lines.append(f"| {group} | {len(ids)} | {', '.join(ids)} |")
    lines += [
        "",
        "## Every original failure, before and after",
        "",
        "| Target | Before | After, 3 runs | Explanation |",
        "| --- | --- | --- | --- |",
    ]
    for r in rows:
        status = ", ".join(f"{k} ({v})" for k, v in r["after_outcomes"].items())
        lines.append(
            f"| {r['id']} | {r['before_outcome']} | {r['passes']}/3 content checks; {status} | {r['explanation']} |"
        )
    lines += [
        "",
        "## Engine changes",
        "",
        "An explicit redirect-origin permission now allows public helper hops without adding them to the frontier or authorizing arbitrary initial requests. Helper robots, DNS/public-address validation, exclusions, request/byte limits and GET/HEAD restrictions still apply. Nature follows about → journal-information → authorized helper → public journal-information. The server returns the article with cookies_not_supported in its query. The actual main content is extracted; no cookie jar, credential or challenge execution is enabled.",
        "",
        "Robots 429/5xx and transport failures now get bounded retries through the same per-origin scheduler. Retry-After is respected. Persistent failures still prevent content fetches. Robots 401/403 is correctly reported as robots_unavailable instead of pretending a parsed disallow rule exists. Explicit parsed rules remain robots_denied.",
        "",
        "Source titles are preserved consistently in all accepted candidate text before verification and optional candidate selection. Quality scores are not increased by adding a title. Windy already supplied working forecast data, but its literal forecast word existed in the actual title. The exported document now retains it. No Windy-specific keyword is inserted and the old metric is unchanged.",
        "",
        "Network failures now record their phase, budget, error class and OS errno when available. The HTTP/2 investigation did not repair NHK, so no unproven extra transport dependency was added.",
        "",
        "## Results and limits",
        "",
        "| Final run | Reviewed content | Requests | Chromium pages |",
        "| --- | --- | --- | --- |",
    ]
    for i, _r in enumerate(runs):
        lines.append(
            f"| {i + 1} | {summary['scores'][i]}/50 | {summary['requests'][i]} | {summary['browser_pages'][i]} |"
        )
    lines += [
        "",
        "Additional failures outside the original 17 are retained below. They are not hidden by the earlier successful runs.",
        "",
        "| Run | Additional target | Outcome | Reason |",
        "| --- | --- | --- | --- |",
    ]
    for failure in additional:
        lines.append(
            f"| {failure['run']} | {failure['id']} | {failure['outcome']} | {failure['reason']} |"
        )
    lines += [
        "",
        "Nature helper authorization is a configuration change; title preservation, retries and diagnostics are engine changes. These runs use a repeated development set, not unseen websites. Changes in coverage are not a statistical speed or generalization claim.",
        "",
        "Eight access walls, two explicit robots exclusions and three unreadable robots files cannot be converted into extracted content under the current no-bypass constraints. Repeated requests may succeed later if the site changes its policy or availability; current results remain refusals. IKEA does not return the specified product. NHK does not return a usable response from this machine through the tested permitted transports. No alternative site, different product or cached provider result is presented as the original target.",
        "",
        "All 90 automated tests pass. Nine added cases cover transient-policy retries, unreadable-policy classification, request refusal, helper authorization, helper robots, helper frontier isolation, title preservation and actionable timeouts. Retained bodies are hash-verified. These are local tests and inline review, not an independent security audit.",
        "",
        "The eight access pages were inspected directly. HTTP 200 is not taken as proof of content: USGS returns a robot check at 202, while Patagonia and Khan Academy return queue/challenge pages at 200. All remain terminal refusals.",
        "",
        "## Working example",
        "",
        "```sh",
        "cd /Users/aditya/Me/optimal-crawler",
        "bash scripts/demo-adaptive.sh",
        "```",
        "",
        "The demo uses the same explicit helper and dependency permissions and exports actual Nature and Windy documents. It requires no key.",
        "",
        "## Evidence",
        "",
        "- [All 17 machine-readable outcomes](comparison.json).",
        "- [All 17 as CSV](remaining17.csv).",
        "- [Current-source reproduction before fixes](before17/run-0.json).",
        "- [Final run 1](final50/run-0.json), [run 2](final50/run-1.json), [run 3](final50/run-2.json).",
        "- [Guarded transport diagnostics](diagnostics/summary.json), [longer NHK request](nhk-long/result.json).",
        "- [Final authorization profile](final50/profile.json), [source fingerprint](final50/phase.json).",
        "",
        "The HTTP/2 diagnostic used a public-IP-pinned curl request only after the crawler allowed the URL through robots. It used verified TLS, the same crawler user agent, no proxy and no redirect following. The [HTTP/2 protocol documentation](https://www.encode.io/httpcore/http2/) describes ordinary negotiated HTTP/2; changing protocol did not resolve the observed NHK stream error.",
        "",
        f"Final source SHA-256: `{digest}`.",
        "",
    ]
    md = "\n".join(lines)
    (ROOT / "README.md").write_text(md)
    rendered = MarkdownIt("commonmark", {"html": False}).render(md)
    page = (
        '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Prism adaptive failure audit</title><style>body{font:16px/1.6 system-ui;max-width:1200px;margin:40px auto;padding:0 24px;color:#17232e;background:#fbfcfd}table{border-collapse:collapse;display:block;overflow:auto;width:100%}td,th{border:1px solid #cad3db;padding:10px;text-align:left}pre{white-space:pre-wrap;background:#edf2f5;padding:20px}a{color:#075cb0}h1{line-height:1.2}</style><main>'
        + rendered
        + "</main></html>"
    )
    (ROOT / "index.html").write_text(page)
    documentation = md
    for prefix in [
        "comparison.json",
        "remaining17.csv",
        "before17/",
        "final50/",
        "diagnostics/",
        "nhk-long/",
    ]:
        documentation = documentation.replace("](" + prefix, "](../reports/adaptive/" + prefix)
    Path("docs/adaptive-recovery.md").write_text(documentation)
    print(json.dumps({k: v for k, v in summary.items() if k != "remaining17"}, indent=2))


if __name__ == "__main__":
    main()
