"""Collect source-only reference proposals before looking at crawler outputs."""

import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright

from benchmarks.oss_policy import UA, Policy, observed_assets, origin
from benchmarks.oss_quality import shingles, tokens


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str))


def propose(raw, root_xpath=None, exclude_xpaths=(), include_leaf_text=False):
    import html5lib
    from lxml import etree, html

    parsed = html5lib.parse(raw, treebuilder="lxml", namespaceHTMLElements=False).getroot()
    tree = html.fromstring(etree.tostring(parsed, method="xml"))
    for element in tree.xpath('//script|//style|//noscript|//*[@hidden]|//*[@aria-hidden="true"]'):
        element.drop_tree()
    roots = tree.xpath(root_xpath) if root_xpath else tree.xpath('//main|//*[@role="main"]')
    if not roots:
        articles = tree.xpath("//article")
        roots = articles if len(articles) == 1 else []
    root = max(roots, key=lambda el: len(el.text_content()), default=tree)
    root_path = tree.getroottree().getpath(root)
    outside = []
    for el in tree.xpath('//nav|//footer|//aside|//header[@role="banner"]'):
        if el is root or root in el.iterancestors():
            # Navigation within the chosen content container is still not reference prose.
            pass
        text = " ".join(el.text_content().split())
        if 8 <= len(tokens(text)) <= 120:
            outside.append(text)
    for selector in exclude_xpaths:
        for el in tree.xpath(selector):
            el.drop_tree()
    for el in root.xpath(".//aside|.//form"):
        el.drop_tree()
    selected = []
    for el in root.xpath(
        ".//h1|.//h2|.//h3|.//h4|.//h5|.//h6|.//p|.//li|.//pre|.//blockquote|.//td|.//th|.//dd"
    ):
        if any(parent in selected for parent in el.iterancestors()):
            continue
        value = " ".join(el.text_content().split())
        if len(tokens(value)) >= (1 if include_leaf_text else 3) or (
            el.tag in {"h1", "h2", "h3", "h4", "h5", "h6"} and len(value) >= 3
        ):
            selected.append(el)
    blocks = [{"tag": el.tag, "text": " ".join(el.text_content().split())} for el in selected]
    if include_leaf_text:
        # Explicit v2 reference construction. Keep the original frozen development
        # labels; use this only in a labelled sensitivity analysis and new holdouts.
        chosen = set(selected)
        block_tags = {"div", "section", "article", "main", "nav", "ul", "ol", "dl", "table",
                      "tbody", "thead", "tr", "td", "th", "p", "li", "dd", "dt", "pre",
                      "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "figure", "figcaption"}

        def walk(node):
            if node in chosen:
                return [{"tag": node.tag, "text": " ".join(node.text_content().split())}]
            if node.tag in {"button", "input", "select", "textarea"}:
                return []
            result, pending = [], [node.text or ""]

            def flush():
                value = " ".join("".join(pending).split())
                if len(tokens(value)) >= 1:
                    result.append({"tag": "unwrapped", "text": value})
                pending.clear()

            for child in node:
                if not isinstance(child.tag, str):
                    pending.append(child.tail or "")
                    continue
                if child.tag in block_tags or child in chosen or any(el in chosen for el in child.iterdescendants()):
                    flush()
                    result.extend(walk(child))
                elif child.tag not in {"button", "input", "select", "textarea"}:
                    pending.append(child.text_content())
                pending.append(child.tail or "")
            flush()
            return result

        blocks = walk(root)
    reference = "\n".join(b["text"] for b in blocks)
    reference_chunks = shingles(tokens(reference))
    negatives = [
        b
        for b in outside
        if len(shingles(tokens(b)) & reference_chunks) / max(1, len(shingles(tokens(b)))) < 0.2
    ]
    return {
        "root_xpath": root_path,
        "blocks": blocks,
        "headings": [
            " ".join(el.text_content().split())
            for el in root.xpath(".//h1|.//h2|.//h3|.//h4|.//h5|.//h6")
            if len(el.text_content().strip()) >= 3
        ],
        "code": [b["text"] for b in blocks if b["tag"] == "pre"],
        "boilerplate": negatives,
        "reference_chars": len(reference),
        "reviewed": False,
        "reference_schema": "semantic-and-unwrapped-v2" if include_leaf_text else "semantic-blocks-v1",
        "min_block_tokens": 1 if include_leaf_text else 3,
    }


async def collect(args):
    root = args.output
    root.mkdir(parents=True, exist_ok=False)
    cases = [
        c
        for c in json.loads(Path("benchmarks/oss50.json").read_text())
        if c["cohort"] == args.cohort
    ]
    results = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            for case in cases:
                folder = root / case["id"]
                folder.mkdir()
                case = dict(case)
                began = time.monotonic()
                row = {"case": case, "eligibility": "reference_error", "reason": ""}
                async with Policy(case, folder) as policy:
                    try:
                        async with asyncio.timeout(45):
                            initial = await policy.fetch(case["url"])
                            # Permit an observed www/apex/subdomain redirect only after recording it.
                            if initial["reason"] == "out_of_scope":
                                destination = initial["url"]
                                base = urlsplit(case["url"]).hostname.removeprefix("www.")
                                host = urlsplit(destination).hostname or ""
                                if host == base or host.endswith("." + base):
                                    case["redirect_origins"] = [origin(destination)]
                                    policy.origins.add(origin(destination))
                                    initial = await policy.fetch(case["url"])
                            case["asset_origins"] = observed_assets(initial["body"], initial["url"])
                            policy.origins.update(case["asset_origins"])
                            if policy.cf:
                                row.update(
                                    eligibility="cloudflare_skip",
                                    reason="Confirmed challenge during reference qualification",
                                )
                            elif initial["reason"]:
                                row.update(
                                    eligibility="unavailable",
                                    reason=initial["reason"],
                                    status=initial["status"],
                                )
                            else:
                                raw = initial["body"].decode("utf-8", "replace")
                                # Every eligible reference also gets a native browser view. No Prism parser/verifier.
                                context = await browser.new_context(
                                    user_agent=UA,
                                    service_workers="block",
                                    ignore_https_errors=False,
                                )
                                try:
                                    await policy.attach(context)
                                    page = await context.new_page()
                                    policy.pages += 1
                                    try:
                                        response = await page.goto(
                                            case["url"],
                                            wait_until="domcontentloaded",
                                            timeout=30000,
                                        )
                                        previous = ""
                                        stable = 0
                                        for _ in range(12):
                                            await page.wait_for_timeout(250)
                                            current = await page.locator("body").inner_text(
                                                timeout=1000
                                            )
                                            stable = stable + 1 if current == previous else 0
                                            previous = current
                                            if stable >= 4:
                                                break
                                        rendered = await page.content()
                                        if (
                                            not policy.cf
                                            and response
                                            and 200 <= response.status < 300
                                        ):
                                            raw = rendered
                                    except Exception as exc:
                                        row["browser_reference_note"] = str(exc)[:240]
                                finally:
                                    await context.close()
                                if policy.cf:
                                    row.update(
                                        eligibility="cloudflare_skip",
                                        reason="Confirmed challenge in native reference browser",
                                    )
                                else:
                                    (folder / "reference.html").write_text(raw)
                                    proposal = propose(raw)
                                    save(folder / "proposal.json", proposal)
                                    row.update(
                                        eligibility="review_pending",
                                        status=initial["status"],
                                        reference_sha256=hashlib.sha256(raw.encode()).hexdigest(),
                                        preview={
                                            "root": proposal["root_xpath"],
                                            "chars": proposal["reference_chars"],
                                            "headings": proposal["headings"][:12],
                                            "first": proposal["blocks"][:2],
                                            "last": proposal["blocks"][-2:],
                                        },
                                    )
                    except Exception as exc:
                        row["reason"] = f"{type(exc).__name__}: {str(exc)[:250]}"
                row["elapsed_s"] = time.monotonic() - began
                save(folder / "reference.json", row)
                results.append(row)
                save(root / "progress.json", results)
                print(
                    json.dumps({k: row[k] for k in ["case", "eligibility", "reason", "elapsed_s"]}),
                    flush=True,
                )
        finally:
            await browser.close()
    save(root / "summary.json", results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort", choices=["development", "reserved"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(collect(parser.parse_args()))
