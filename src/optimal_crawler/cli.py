import asyncio
import hashlib
import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .crawler import Crawler
from .models import CrawlConfig, Strategy
from .store import Store

app = typer.Typer(no_args_is_help=True, help="Prism: adaptive, polite crawling with verified content.")
console = Console()


@app.command()
def crawl(
    urls: list[str] = typer.Argument(..., help="Seed URLs. Each origin becomes an explicit scope."),
    db: Path = typer.Option(Path("data/crawl.db"), help="SQLite checkpoint and telemetry file."),
    strategy: Strategy = typer.Option(Strategy.ADAPTIVE, hidden=True),
    max_pages: int = 50,
    max_requests: int = 500,
    max_depth: int = 4,
    workers: int = 4,
    asset_concurrency: int = 4,
    asset_delay: float = 0.05,
    delay: float = 0.5,
    timeout: float = 20,
    browser_timeout: float = 90,
    run_timeout: float = 600,
    render_wait: float = 2.5,
    scroll_steps: int = 6,
    load_styles: bool = True,
    max_bytes: int = 16_000_000,
    discover: bool = True,
    follow_links: bool = True,
    allow_private: bool = typer.Option(False, help="Explicitly allow local/private sites, for trusted test environments."),
    asset_origin: list[str] | None = typer.Option(None, help="Additional origins for browser assets only."),
    redirect_origin: list[str] | None = typer.Option(None, help="Authorize a public redirect helper; its robots rules still apply. Does not add frontier seeds."),
    exclude_origin: list[str] | None = typer.Option(None, help="Exclude a site, for example because its terms disallow crawling."),
    user_agent: str = "PrismCrawler/0.1 (+local research crawler)",
    decision_provider: str = typer.Option("none", help="none, laya, or systemone."),
    decision_endpoint: str = "http://127.0.0.1:8019",
    decision_model: str = "kev-latest",
    laya_model: str = "multilingual",
    decision_mode: str = typer.Option("shadow", help="shadow records advice; assist can select an existing parser candidate."),
    decision_threshold: float = 0.9,
    resume: bool = False,
):
    config = CrawlConfig(
        db=db, strategy=strategy, max_pages=max_pages, max_requests=max_requests,
        max_depth=max_depth, workers=workers, min_delay=delay, timeout=timeout,
        asset_concurrency=asset_concurrency, asset_delay=asset_delay,
        browser_timeout=browser_timeout,
        run_timeout=run_timeout, render_wait=render_wait, scroll_steps=scroll_steps, max_bytes=max_bytes,
        load_styles=load_styles,
        discover=discover, follow_links=follow_links, allow_private=allow_private,
        asset_origins=tuple(asset_origin or ()), excluded_origins=tuple(exclude_origin or ()),
        redirect_origins=tuple(redirect_origin or ()),
        user_agent=user_agent, decision_provider=decision_provider, decision_endpoint=decision_endpoint,
        decision_model=decision_model, decision_mode=decision_mode, decision_threshold=decision_threshold,
        laya_model=laya_model,
    )

    async def execute():
        async with Crawler(config) as crawler:
            return await crawler.run(urls, resume=resume)

    try:
        summary = asyncio.run(execute())
    except (ValueError, RuntimeError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    console.print_json(data=summary)
    if summary["status"] == "interrupted":
        raise typer.Exit(3)
    if not sum(summary["outcomes"].get(outcome, 0) for outcome in ("ok", "unchanged")):
        raise typer.Exit(1)


@app.command("inspect")
def inspect_db(db: Path = Path("data/crawl.db"), run_id: str = ""):
    """Show actual page results from a saved run."""
    if not db.exists():
        raise typer.BadParameter("Database does not exist")
    store = Store(db)
    try:
        latest = store.latest()
        if not latest:
            console.print("No runs saved.")
            return
        run_id = run_id or latest["id"]
        table = Table("Outcome", "Method", "Parser", "Characters", "URL", "Reason")
        for document in store.documents(run_id):
            table.add_row(document["outcome"], document["method"], document["parser"],
                          str(len(document["text"])), document["url"], document["reason"])
        console.print(table)
    finally:
        store.close()


@app.command()
def export(db: Path = Path("data/crawl.db"), output: Path = Path("data/pages.jsonl"),
           format: str = "jsonl", run_id: str = ""):
    """Export extracted content with provenance and refusal reasons."""
    if format not in {"jsonl", "markdown"}:
        raise typer.BadParameter("format must be jsonl or markdown")
    if not db.exists():
        raise typer.BadParameter("Database does not exist")
    store = Store(db)
    try:
        latest = store.latest()
        if not latest:
            raise typer.BadParameter("Database contains no runs")
        documents = store.documents(run_id or latest["id"])
        if format == "jsonl":
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("".join(json.dumps(d, ensure_ascii=False) + "\n" for d in documents))
        else:
            output.mkdir(parents=True, exist_ok=True)
            for document in documents:
                name = hashlib.sha256(document["url"].encode()).hexdigest()[:16] + ".md"
                (output / name).write_text(f"# {document['title'] or document['url']}\n\n"
                                          f"Source: {document['final_url']}\n\n"
                                          f"Outcome: {document['outcome']}\n\n{document['text']}\n")
        console.print(f"Exported {len(documents)} page records to {output}")
    finally:
        store.close()


@app.command()
def recrawl(db: Path = Path("data/crawl.db"), loop: bool = False, tick: float = 60):
    """Revisit due pages using the saved configuration and conditional requests."""
    if not db.exists() or tick < 1:
        raise typer.BadParameter("Existing database and tick >= 1 are required")

    async def execute():
        while True:
            store = Store(db)
            latest = store.latest()
            due = store.due()
            store.close()
            if not latest:
                raise ValueError("Database contains no runs")
            if due:
                values = json.loads(latest["config"])
                values["db"] = db
                values["strategy"] = Strategy(values["strategy"])
                values["discover"], values["follow_links"] = False, False
                async with Crawler(CrawlConfig(**values)) as crawler:
                    summary = await crawler.run(due[:values["max_pages"]])
                    console.print_json(data=summary)
            elif not loop:
                console.print("No pages are due for re-crawl.")
            if not loop:
                break
            await asyncio.sleep(tick)

    asyncio.run(execute())


if __name__ == "__main__":
    app()
