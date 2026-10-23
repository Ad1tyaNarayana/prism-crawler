import sqlite3
from dataclasses import asdict

import pytest

from optimal_crawler.models import CrawlConfig, Document, Outcome
from optimal_crawler.store import Store


def test_page_and_discovered_children_commit_together(tmp_path):
    config = CrawlConfig(db=tmp_path / "crawl.db")
    store = Store(config.db)
    seed = "https://example.com/"
    run = store.start(config, [seed])
    store.enqueue(run, seed, 0, 10)
    store.claim(run, set())
    document = Document(seed, seed, Outcome.OK, "http", text="Original source record")
    children = [("https://example.com/child", 1)]
    store.record(run, document, config, discovered=children)
    store.close()
    recovered = Store(config.db)
    assert recovered.start(config, [seed], resume=True) == run
    assert recovered.documents(run) == [asdict(document)]
    assert recovered.claim(run, set())["url"] == children[0][0]
    recovered.close()


def test_failed_child_insert_rolls_back_page_completion(tmp_path):
    config = CrawlConfig(db=tmp_path / "crawl.db")
    store = Store(config.db)
    seed = "https://example.com/"
    run = store.start(config, [seed])
    store.enqueue(run, seed, 0, 10)
    store.claim(run, set())
    store.db.execute("""CREATE TRIGGER simulate_disk_failure BEFORE INSERT ON frontier
                        WHEN NEW.depth=1 BEGIN SELECT RAISE(ABORT, 'injected failure'); END""")
    document = Document(seed, seed, Outcome.OK, "http", text="Original source record")
    with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
        store.record(run, document, config, discovered=[("https://example.com/child", 1)])
    assert store.documents(run) == []
    assert store.previous(seed) is None
    assert store.db.execute("SELECT state FROM frontier").fetchone()[0] == "leased"
    store.close()
