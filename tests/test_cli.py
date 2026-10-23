import asyncio
import sys

from benchmarks.fixture import site


async def test_cli_nonzero_when_no_content_is_available(tmp_path):
    async with site() as fixture:
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "optimal_crawler.cli", "crawl", fixture.base + "/private/secret",
            "--db", str(tmp_path / "cli.db"), "--allow-private", "--no-discover", "--no-follow-links",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        assert process.returncode == 1, stderr.decode()
        assert "robots_denied" in stdout.decode()
        assert fixture.counts["/private/secret"] == 0
