"""Validate all adapters on local fixtures before a public-site observation."""

import argparse
import asyncio
import json
from pathlib import Path

from benchmarks.fixture import site
from benchmarks.oss_trial import trial


async def main(root):
    root.mkdir(exist_ok=False)
    rows = []
    async with site() as fixture:
        for provider in ["prism", "crawlee", "crawl4ai"]:
            for path, marker in [
                ("/static", "violet comets"),
                ("/js", "coastal archive"),
                ("/private/secret", None),
            ]:
                case = {"id": "fixture", "url": fixture.base + path}
                result = await trial(
                    provider, case, root / (provider + "-" + path.rsplit("/", 1)[-1]), True
                )
                passed = (
                    marker in result["text"]
                    if marker
                    else not result["text"] and fixture.counts["/private/secret"] == 0
                )
                if marker is None and result.get("outcome") != "robots_denied":
                    gate = json.loads(
                        (
                            root / (provider + "-" + path.rsplit("/", 1)[-1]) / "policy.json"
                        ).read_text()
                    )
                    passed = passed and any(d["reason"] == "robots_denied" for d in gate["denied"])
                rows.append(
                    {
                        "provider": provider,
                        "path": path,
                        "passed": passed,
                        **{
                            k: result.get(k)
                            for k in [
                                "outcome",
                                "reason",
                                "browser_pages",
                                "browser_launches",
                                "end_to_end_s",
                            ]
                        },
                    }
                )
                print(json.dumps(rows[-1]), flush=True)
    (root / "summary.json").write_text(json.dumps(rows, indent=2))
    assert all(r["passed"] for r in rows), rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(main(parser.parse_args().output))
