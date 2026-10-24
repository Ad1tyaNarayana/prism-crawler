"""Subprocess orchestration shared by fixture calibration and public-site trials."""

import asyncio
import json
import os
import signal
import subprocess
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PYTHONS = {
    "prism": REPO / ".venv/bin/python",
    "crawlee": REPO / ".runtime/crawlee-env/bin/python",
    "crawl4ai": REPO / ".runtime/crawl4ai-env/bin/python",
}


def rss(pid):
    raw = subprocess.check_output(["ps", "-axo", "pid=,ppid=,rss="], text=True)
    rows = [tuple(map(int, line.split())) for line in raw.splitlines() if line.strip()]
    ids = {pid}
    while True:
        children = {p for p, parent, _ in rows if parent in ids}
        if children <= ids:
            break
        ids |= children
    return sum(memory for p, _, memory in rows if p in ids) * 1024


async def trial(provider, case, folder, private=False):
    folder = Path(folder).resolve()
    folder.parent.mkdir(parents=True, exist_ok=True)
    casefile = folder.parent / (folder.name + "-case.json")
    casefile.write_text(json.dumps(case, indent=2))
    env = {
        **os.environ,
        "PYTHONPATH": str(REPO) + ":" + str(REPO / "src"),
        "CRAWL4AI_SUPPRESS_CLOUD_NOTICE": "1",
        "TOKENIZERS_PARALLELISM": "false",
    }
    command = [
        str(PYTHONS[provider]),
        "-m",
        "benchmarks.oss_worker",
        "--provider",
        provider,
        "--case",
        str(casefile),
        "--output",
        str(folder),
    ]
    if private:
        command += ["--allow-private"]
    start = time.monotonic()
    peak = 0
    timedout = False
    with (folder.parent / (folder.name + ".log")).open("w") as log:
        process = await asyncio.create_subprocess_exec(
            *command, cwd=folder.parent, env=env, stdout=log, stderr=log, start_new_session=True
        )
        while process.returncode is None:
            peak = max(peak, await asyncio.to_thread(rss, process.pid))
            if time.monotonic() - start > 65:
                timedout = True
                os.killpg(process.pid, signal.SIGKILL)
                await process.wait()
                break
            try:
                await asyncio.wait_for(process.wait(), 0.25)
            except TimeoutError:
                pass
    result = (
        json.loads((folder / "result.json").read_text())
        if (folder / "result.json").exists()
        else {
            "provider": provider,
            "outcome": "error",
            "reason": "Hard subprocess timeout" if timedout else "Adapter exited without result",
            "text": "",
        }
    )
    result.update(
        end_to_end_s=time.monotonic() - start,
        peak_sampled_rss_bytes=peak,
        exit_code=process.returncode,
        hard_timeout=timedout,
    )
    folder.mkdir(exist_ok=True)
    (folder / "receipt.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False, default=str)
    )
    return result
