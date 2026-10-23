import asyncio
import json
from pathlib import Path

from optimal_crawler.decisions import ROUTE_QUESTION, DecisionEngine
from optimal_crawler.models import CrawlConfig


async def main():
    engine = DecisionEngine(CrawlConfig(decision_provider="laya"))
    result = await engine.decide("The page contains an empty app div and says Loading. "
                                 "A script fetches an article and renders it after JavaScript runs.",
                                 ROUTE_QUESTION, "installation-smoke")
    Path(".audit/laya-smoke.json").write_text(json.dumps({"result": result,
                                             "cold_ms": engine.elapsed_ms}, indent=2))
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
