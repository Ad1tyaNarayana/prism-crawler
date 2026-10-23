"""Local end-to-end load and SIGKILL recovery experiments with origin-side evidence."""

import argparse
import asyncio
import json
import os
import resource
import signal
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from aiohttp import web

from benchmarks.provider_live_report import distribution
from benchmarks.real50 import runtime_digest
from optimal_crawler.crawler import Crawler
from optimal_crawler.models import CrawlConfig
from optimal_crawler.store import Store


def save(path, value):
    path.write_text(json.dumps(value, indent=2, default=str))


def article(token):
    paragraphs = [
        f"Document token {token} identifies this original research record. Reliable collection requires each published record to remain associated with its requested address and its source evidence. This paragraph is part of the expected document, not navigation or generated extraction.",
        "A persistent frontier schedules pending work across independent origins. Each origin receives bounded concurrency so that a slow response does not prevent unrelated sources from progressing. Completed work is stored before the next visit is scheduled.",
        "The experiment measures useful document throughput together with request latency and memory. Successful extraction must preserve the source marker and the final paragraph. Fast error responses are reported separately and never counted as completed useful documents.",
        f"The final evidence marker is END-{token}. Recovery should preserve every committed record and revisit interrupted requests when necessary. Duplicate network requests can occur after a crash; duplicate stored records must not appear in the exported result.",
    ]
    return "".join(f"<p>{p}</p>" for p in paragraphs)


async def worker(args):
    values = json.loads(args.config.read_text())
    values["db"] = Path(values["db"])
    config = CrawlConfig(**values)
    seeds = json.loads(args.seeds.read_text())
    if args.crash_after_record:
        original = Store.record

        def crash_record(instance, *a, **kw):
            original(instance, *a, **kw)
            os.kill(os.getpid(), signal.SIGKILL)

        Store.record = crash_record
    started = time.monotonic()
    async with Crawler(config) as crawler:
        summary = await crawler.run(seeds, resume=args.resume)
        docs = crawler.store.documents(crawler.run_id)
        requests = crawler.store.events("requests", crawler.run_id)
    own = resource.getrusage(resource.RUSAGE_SELF)
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    save(
        args.result,
        {
            "summary": summary,
            "wall_s": time.monotonic() - started,
            "cpu_self_s": own.ru_utime + own.ru_stime,
            "cpu_reaped_children_s": children.ru_utime + children.ru_stime,
            "documents": docs,
            "requests": requests,
        },
    )


def process_tree_rss(pid):
    raw = subprocess.check_output(["ps", "-axo", "pid=,ppid=,rss="], text=True)
    processes = [tuple(map(int, line.split())) for line in raw.splitlines() if line.strip()]
    ids = {pid}
    while True:
        descendants = {p for p, pp, _ in processes if pp in ids}
        if descendants <= ids:
            break
        ids |= descendants
    return sum(rss for p, _, rss in processes if p in ids) * 1024


async def suite(args):
    root = args.output
    root.mkdir(parents=True, exist_ok=False)
    ledger = []
    runners = []
    origins = []
    counts = Counter()
    active = Counter()
    max_active = Counter()
    run_name = "setup"
    source_hash = runtime_digest()

    async def handler(request):
        ident = int(request.app["origin_id"])
        path = request.path
        counts[(run_name, ident, path)] += 1
        active[ident] += 1
        max_active[ident] = max(max_active[ident], active[ident])
        began = time.monotonic()
        status = 200
        try:
            if path == "/robots.txt":
                return web.Response(text="User-agent: *\nDisallow: /denied\n")
            if path == "/denied":
                return web.Response(status=500, text="Forbidden endpoint reached")
            await asyncio.sleep(args.fixture_delay)
            token = f"o{ident}-" + ("index" if path == "/" else path.rsplit("/", 1)[-1])
            if path.startswith("/page/"):
                n = int(path.rsplit("/", 1)[-1])
                if args.mixed and n % 50 == 0 and counts[(run_name, ident, path)] == 1:
                    status = 503
                    return web.Response(
                        status=503,
                        headers={"Retry-After": ".05"},
                        text="Temporary server maintenance",
                    )
            text = article(token)
            nav = ""
            if path == "/":
                nav = (
                    "<nav>"
                    + "".join(
                        f'<a href="/page/{n}">Record {n}</a>' for n in range(args.pages_per_origin)
                    )
                    + '<a href="/denied">Denied by policy</a></nav>'
                )
            js = args.mixed and path.startswith("/page/") and int(path.rsplit("/", 1)[-1]) % 40 == 1
            body = (
                f'<main id="content"><p>Loading...</p></main><script>setTimeout(()=>{{document.querySelector("main").innerHTML={json.dumps(text)}}},40)</script>'
                if js
                else f"<main><h1>Record {token}</h1>{text}</main>"
            )
            return web.Response(
                text=f"<html><head><title>Research record {token}</title></head><body>{body}{nav}</body></html>",
                content_type="text/html",
            )
        finally:
            active[ident] -= 1
            ledger.append(
                {
                    "trial": run_name,
                    "origin": ident,
                    "path": path,
                    "status": status,
                    "ms": (time.monotonic() - began) * 1000,
                }
            )

    try:
        for ident in range(args.origins):
            app = web.Application()
            app["origin_id"] = ident
            app.router.add_get("/{tail:.*}", handler)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            origins.append(f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}")
            runners.append(runner)
        save(
            root / "phase.json",
            {
                "started_at": datetime.now(UTC).isoformat(),
                "source_sha256": source_hash,
                "origins": args.origins,
                "pages_per_origin": args.pages_per_origin,
                "mixed": args.mixed,
                "fixture_delay_ms": args.fixture_delay * 1000,
                "rss_sampling_ms": 250,
                "claim": "Single-host synthetic workload including actual HTTP, parsing, SQLite and fixture server overhead; not public-web throughput.",
            },
        )
        summaries = []
        trials = [(f"w{w}", w, False) for w in args.workers]
        if args.recovery:
            trials += [("recovery-inflight", 4, True), ("recovery-commit-window", 4, True)]
        for name, workers, recover in trials:
            assert runtime_digest() == source_hash, "Source changed during load suite"
            run_name = name
            folder = root / name
            folder.mkdir()
            max_active.clear()
            config = CrawlConfig(
                db=folder / "crawl.db",
                allow_private=True,
                workers=workers,
                browser_concurrency=2,
                min_delay=0,
                max_delay=1,
                asset_delay=0,
                max_pages=args.origins * (args.pages_per_origin + 2),
                max_frontier=max(10000, args.origins * (args.pages_per_origin + 2) + 100),
                max_requests=30000,
                max_depth=1,
                discover=False,
                follow_links=True,
                timeout=3,
                browser_timeout=8,
                run_timeout=240,
                render_wait=0.1,
                scroll_steps=0,
                retries=1,
            )
            save(folder / "config.json", config.to_dict())
            save(folder / "seeds.json", origins)
            base = [
                sys.executable,
                "-m",
                "benchmarks.load_recovery",
                "worker",
                "--config",
                str(folder / "config.json"),
                "--seeds",
                str(folder / "seeds.json"),
                "--result",
                str(folder / "worker.json"),
            ]
            samples = []
            exits = []
            snapshots = []
            start = time.monotonic()
            recovery_started = None
            for attempt in range(2 if recover else 1):
                command = base + (["--resume"] if attempt else [])
                if name == "recovery-commit-window" and not attempt:
                    command += ["--crash-after-record"]
                with (folder / f"worker-{attempt}.log").open("w") as log:
                    proc = await asyncio.create_subprocess_exec(*command, stdout=log, stderr=log)
                    killed = False
                    while proc.returncode is None:
                        samples.append(
                            {
                                "elapsed_s": time.monotonic() - start,
                                "attempt": attempt,
                                "rss_bytes": await asyncio.to_thread(process_tree_rss, proc.pid),
                            }
                        )
                        if (
                            name == "recovery-inflight"
                            and attempt == 0
                            and not killed
                            and config.db.exists()
                        ):
                            try:
                                with sqlite3.connect(config.db) as db:
                                    finished = db.execute(
                                        "SELECT COUNT(*) FROM results"
                                    ).fetchone()[0]
                                    leased = db.execute(
                                        "SELECT COUNT(*) FROM frontier WHERE state='leased'"
                                    ).fetchone()[0]
                                if finished >= min(100, args.pages_per_origin) and leased:
                                    proc.kill()
                                    killed = True
                            except sqlite3.Error:
                                pass
                        try:
                            await asyncio.wait_for(proc.wait(), 0.25)
                        except TimeoutError:
                            pass
                exits.append(proc.returncode)
                if attempt == 0 and recover:
                    with sqlite3.connect(config.db) as db:
                        snapshots.append(
                            {
                                "completed": db.execute("SELECT COUNT(*) FROM results").fetchone()[
                                    0
                                ],
                                "states": dict(
                                    db.execute("SELECT state,COUNT(*) FROM frontier GROUP BY state")
                                ),
                            }
                        )
                    recovery_started = time.monotonic()
            wall = time.monotonic() - start
            data = (
                json.loads((folder / "worker.json").read_text())
                if (folder / "worker.json").exists()
                else {}
            )
            docs = data.get("documents", [])
            ok = [d for d in docs if d["outcome"] == "ok"]
            expected = {
                f"{base}/page/{n}" for base in origins for n in range(args.pages_per_origin)
            } | set(origins)
            actual = {
                d["url"].rstrip("/") if d["url"].rstrip("/") in origins else d["url"] for d in ok
            }
            marker_errors = []
            for d in ok:
                ident = next(i for i, o in enumerate(origins) if d["url"].startswith(o + "/"))
                token = f"o{ident}-" + (
                    "index"
                    if d["url"].rstrip("/") == origins[ident]
                    else d["url"].rsplit("/", 1)[-1]
                )
                if f"Document token {token}" not in d["text"] or f"END-{token}" not in d["text"]:
                    marker_errors.append(d["url"])
            with sqlite3.connect(config.db) as db:
                integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
            requests = [r for r in ledger if r["trial"] == name]
            summary = {
                "trial": name,
                "workers": workers,
                "exit_codes": exits,
                "expected_ok": len(expected),
                "ok": len(ok),
                "missing_urls": sorted(expected - actual),
                "unexpected_urls": sorted(actual - expected),
                "marker_errors": marker_errors,
                "unique_documents": len({d["url"] for d in docs}),
                "documents": len(docs),
                "wall_s": wall,
                "useful_documents_per_s": len(ok) / wall,
                "successful_page_latency": distribution([d["elapsed_ms"] for d in ok]),
                "peak_sampled_process_tree_rss_bytes": max(
                    (s["rss_bytes"] for s in samples), default=0
                ),
                "cpu_self_s": data.get("cpu_self_s"),
                "cpu_reaped_children_s": data.get("cpu_reaped_children_s"),
                "browser_pages": data.get("summary", {}).get("browser_pages"),
                "summary": data.get("summary"),
                "integrity_check": integrity,
                "crash_snapshots": snapshots,
                "resume_to_completion_s": time.monotonic() - recovery_started
                if recovery_started
                else None,
                "origin_requests": len(requests),
                "duplicate_page_requests": sum(
                    max(0, n - 1)
                    for (trial, _, path), n in counts.items()
                    if trial == name and path.startswith("/page/")
                ),
                "denied_endpoint_requests": sum(r["path"] == "/denied" for r in requests),
                "maximum_origin_active": dict(max_active),
            }
            summary["correct"] = (
                not (
                    summary["missing_urls"]
                    or marker_errors
                    or summary["unexpected_urls"]
                    or summary["denied_endpoint_requests"]
                )
                and integrity == "ok"
                and len(docs) == summary["unique_documents"]
                and exits[-1] == 0
            )
            save(folder / "samples.json", samples)
            save(folder / "result.json", summary)
            summaries.append(summary)
            save(root / "summary.json", summaries)
            print(
                json.dumps(
                    {
                        k: summary[k]
                        for k in [
                            "trial",
                            "correct",
                            "ok",
                            "expected_ok",
                            "wall_s",
                            "useful_documents_per_s",
                            "exit_codes",
                        ]
                    }
                ),
                flush=True,
            )
    finally:
        save(root / "origin-ledger.json", ledger)
        for runner in runners:
            await runner.cleanup()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    w = sub.add_parser("worker")
    w.add_argument("--config", type=Path, required=True)
    w.add_argument("--seeds", type=Path, required=True)
    w.add_argument("--result", type=Path, required=True)
    w.add_argument("--resume", action="store_true")
    w.add_argument("--crash-after-record", action="store_true")
    s = sub.add_parser("suite")
    s.add_argument("--output", type=Path, required=True)
    s.add_argument("--origins", type=int, default=8)
    s.add_argument("--pages-per-origin", type=int, default=200)
    s.add_argument("--workers", type=int, nargs="+", default=[1, 4, 8])
    s.add_argument("--fixture-delay", type=float, default=0.015)
    s.add_argument("--mixed", action="store_true")
    s.add_argument("--recovery", action="store_true")
    args = p.parse_args()
    asyncio.run(worker(args) if args.command == "worker" else suite(args))
