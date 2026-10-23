"""Verify exported results from the real command-line example."""

import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--input", type=Path, default=Path("data/technical-demo.jsonl"))
path = parser.parse_args().input
documents = [json.loads(line) for line in path.read_text().splitlines()]
assert len(documents) == 4
by_url = {d["url"]: d for d in documents}
assert all(d["outcome"] == "ok" for d in documents)
assert "Universal Access to All Knowledge" in by_url["https://archive.org/about/"]["text"]
assert "Physics" in by_url["https://openstax.org/details/books/physics"]["text"]
windy = by_url["https://www.windy.com/"]["text"]
assert "°" in windy and "kt" in windy and "offline" not in windy.lower()
assert "search" in by_url["https://exa.ai/"]["text"].lower()
print(json.dumps({"verified": True, "documents": [
    {"url": d["url"], "method": d["method"], "chars": len(d["text"])} for d in documents]}, indent=2))
