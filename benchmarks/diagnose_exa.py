"""Inspect saved Exa HTML to explain failures without another network request."""

import gzip
import json
from pathlib import Path

from lxml import html

from optimal_crawler.extract import Extractor, text_of
from optimal_crawler.models import FetchResult


def main():
    root = Path("reports/technical/exa")
    findings = []
    for strategy in ("http", "browser", "adaptive"):
        record = json.loads((root / f"run-0-{strategy}.json").read_text())
        for check in record["checks"]:
            if check["passed"]:
                continue
            role = "rendered_dom" if strategy == "browser" else "page"
            captures = [c for c in record["captures"] if c["url"] == check["url"]
                        and c["body_sha256"] and c["role"] == role]
            if not captures:
                captures = [c for c in record["captures"] if c["url"] == check["url"]
                            and c["body_sha256"] and c["role"] != "robots"]
            capture = captures[-1]
            raw = gzip.decompress((root / "bodies" / (capture["body_sha256"] + ".gz")).read_bytes())
            tree = html.fromstring(raw)
            for node in tree.xpath("//script|//style|//noscript|//nav|//footer|//header|//aside|//form|"
                                   "//*[@hidden]|//*[@aria-hidden='true']"):
                node.drop_tree()
            visible = text_of(tree)
            extraction = Extractor().extract(FetchResult(check["url"], 200, raw,
                                                        {"content-type": "text/html; charset=utf-8"}))
            findings.append({"strategy": strategy, "id": check["id"], "url": check["url"],
                             "selected_chars": check["chars"], "cleaned_whole_document_chars": len(visible),
                             "semantic_containers": len(tree.xpath("//article|//main|//*[@role='main']")),
                             "missing_checks": check["missing"], "body_sha256": capture["body_sha256"],
                             "candidate_sizes": [{"parser": c.parser, "chars": len(c.text), "quality": c.quality}
                                                 for c in extraction.candidates],
                             "whole_document_contains_missing_markers": {
                                 " / ".join(group): any(word.lower() in visible.lower() for word in group)
                                 for group in check["missing"]}})
    result = {"network_requests": 0, "basis": "Saved repetition-zero response bodies; source-backed parser diagnosis",
              "findings": findings}
    (root / "diagnosis.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
