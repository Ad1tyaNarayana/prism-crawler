import hashlib
import json
import re
import sqlite3
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from .models import Attempt, CrawlConfig, Document, Outcome
from .urls import origin, route_key


def fingerprint(text: str) -> tuple[str, int]:
    normalized = " ".join(text.lower().split())
    exact = hashlib.sha256(normalized.encode()).hexdigest()
    tokens = re.findall(r"\w+", normalized)
    shingles = [" ".join(tokens[i:i + 3]) for i in range(max(1, len(tokens) - 2))]
    bits = [0] * 64
    for shingle in set(shingles):
        value = int.from_bytes(hashlib.blake2b(shingle.encode(), digest_size=8).digest(), "big")
        for bit in range(64):
            bits[bit] += 1 if value & (1 << bit) else -1
    simhash = sum(1 << bit for bit, weight in enumerate(bits) if weight >= 0)
    return exact, simhash


class Store:
    def __init__(self, path: Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, started REAL, finished REAL, status TEXT,
                config TEXT, seeds TEXT, summary TEXT
            );
            CREATE TABLE IF NOT EXISTS frontier (
                run_id TEXT, url TEXT, origin TEXT, depth INTEGER, state TEXT,
                PRIMARY KEY(run_id,url)
            );
            CREATE INDEX IF NOT EXISTS frontier_ready ON frontier(run_id,state,depth);
            CREATE TABLE IF NOT EXISTS results (
                run_id TEXT, url TEXT, document TEXT, PRIMARY KEY(run_id,url)
            );
            CREATE TABLE IF NOT EXISTS pages (
                url TEXT PRIMARY KEY, document TEXT, digest TEXT, simhash TEXT,
                etag TEXT, modified TEXT, checked REAL, interval REAL, next_due REAL
            );
            CREATE INDEX IF NOT EXISTS pages_digest ON pages(digest);
            CREATE INDEX IF NOT EXISTS pages_due ON pages(next_due);
            CREATE TABLE IF NOT EXISTS bands (
                band INTEGER, value INTEGER, url TEXT, PRIMARY KEY(band,value,url)
            );
            CREATE TABLE IF NOT EXISTS attempts (
                run_id TEXT, ts REAL, data TEXT
            );
            CREATE TABLE IF NOT EXISTS requests (
                run_id TEXT, ts REAL, data TEXT
            );
            CREATE TABLE IF NOT EXISTS decisions (
                run_id TEXT, ts REAL, data TEXT
            );
            CREATE TABLE IF NOT EXISTS routes (
                key TEXT, method TEXT, trials INTEGER, successes INTEGER,
                total_ms REAL, updated REAL, PRIMARY KEY(key,method)
            );
        """)
        self.db.commit()

    def close(self):
        self.db.close()

    def start(self, config: CrawlConfig, seeds: list[str], resume: bool = False) -> str:
        if resume:
            row = self.db.execute(
                "SELECT * FROM runs WHERE status IN ('running','interrupted') ORDER BY started DESC LIMIT 1"
            ).fetchone()
            if row:
                old = json.loads(row["config"])
                if json.loads(row["seeds"]) != seeds or old != config.to_dict():
                    # Tuples become JSON arrays. Compare their serialized representations.
                    if json.dumps(old, sort_keys=True) != json.dumps(config.to_dict(), sort_keys=True) \
                            or json.loads(row["seeds"]) != seeds:
                        raise ValueError("Resume requires the original seeds and configuration")
                with self.db:
                    self.db.execute("UPDATE frontier SET state='queued' WHERE run_id=? AND state='leased'",
                                    (row["id"],))
                    self.db.execute("UPDATE runs SET status='running' WHERE id=?", (row["id"],))
                return row["id"]
            raise ValueError("No interrupted run exists in this database")
        run_id = uuid.uuid4().hex[:12]
        with self.db:
            self.db.execute("INSERT INTO runs VALUES(?,?,NULL,'running',?,?,NULL)",
                            (run_id, time.time(), json.dumps(config.to_dict()), json.dumps(seeds)))
        return run_id

    def enqueue(self, run_id: str, url: str, depth: int, limit: int) -> bool:
        count = self.db.execute("SELECT COUNT(*) FROM frontier WHERE run_id=?", (run_id,)).fetchone()[0]
        if count >= limit:
            return False
        with self.db:
            cur = self.db.execute("INSERT OR IGNORE INTO frontier VALUES(?,?,?,?,'queued')",
                                  (run_id, url, origin(url), depth))
        return cur.rowcount == 1

    def claim(self, run_id: str, busy_origins: set[str]):
        rows = self.db.execute("SELECT url,origin,depth FROM frontier WHERE run_id=? AND state='queued' "
                               "ORDER BY depth,rowid", (run_id,))
        row = next((row for row in rows if row["origin"] not in busy_origins), None)
        if row:
            with self.db:
                self.db.execute("UPDATE frontier SET state='leased' WHERE run_id=? AND url=?",
                                (run_id, row["url"]))
        return row

    def pending(self, run_id: str) -> int:
        return self.db.execute("SELECT COUNT(*) FROM frontier WHERE run_id=? AND state!='done'",
                               (run_id,)).fetchone()[0]

    def previous(self, url: str):
        return self.db.execute("SELECT * FROM pages WHERE url=?", (url,)).fetchone()

    def due(self, now: float | None = None) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT url FROM pages WHERE next_due<=? ORDER BY next_due",
                                             (now if now is not None else time.time(),))]

    def record(self, run_id: str, document: Document, config: CrawlConfig,
               etag: str = "", modified: str = "") -> None:
        previous = self.previous(document.url)
        now = time.time()
        with self.db:
            if document.outcome in {Outcome.OK, Outcome.UNCHANGED}:
                digest, simhash = fingerprint(document.text)
                interval = previous["interval"] if previous else config.initial_interval
                if previous:
                    interval *= 1.6 if digest == previous["digest"] else 0.5
                interval = min(config.recrawl_max, max(config.recrawl_min, interval))
                document.next_due = now + interval
                document.duplicate_of = self.duplicate(document.url, digest, simhash, document.text)
                self.db.execute("INSERT OR REPLACE INTO pages VALUES(?,?,?,?,?,?,?,?,?)", (
                    document.url, json.dumps(asdict(document)), digest, f"{simhash:016x}",
                    etag, modified, now, interval, document.next_due))
                self.db.execute("DELETE FROM bands WHERE url=?", (document.url,))
                self.db.executemany("INSERT INTO bands VALUES(?,?,?)", [
                    (i, (simhash >> (i * 16)) & 65535, document.url) for i in range(4)])
            elif previous:
                document.next_due = now + min(config.recrawl_max, max(config.recrawl_min, 3600))
                self.db.execute("UPDATE pages SET next_due=? WHERE url=?",
                                (document.next_due, document.url))
            self.db.execute("INSERT OR REPLACE INTO results VALUES(?,?,?)",
                            (run_id, document.url, json.dumps(asdict(document))))
            self.db.execute("UPDATE frontier SET state='done' WHERE run_id=? AND url=?",
                            (run_id, document.url))

    def duplicate(self, url: str, digest: str, simhash: int, text: str) -> str | None:
        exact = self.db.execute("SELECT url FROM pages WHERE digest=? AND url!=? LIMIT 1",
                                (digest, url)).fetchone()
        if exact:
            return exact[0]
        if len(text.split()) < 40:
            return None
        candidates = set()
        for band in range(4):
            candidates.update(r[0] for r in self.db.execute(
                "SELECT url FROM bands WHERE band=? AND value=? AND url!=?",
                (band, (simhash >> (band * 16)) & 65535, url)))
        for candidate in sorted(candidates):
            row = self.previous(candidate)
            if (int(row["simhash"], 16) ^ simhash).bit_count() <= 3:
                other_text = json.loads(row["document"])["text"]
                if 0.8 <= len(text) / max(1, len(other_text)) <= 1.25:
                    return candidate
        return None

    def event(self, table: str, run_id: str, data: dict):
        if table not in {"requests", "attempts", "decisions"}:
            raise ValueError("Unknown event table")
        with self.db:
            self.db.execute(f"INSERT INTO {table} VALUES(?,?,?)", (run_id, time.time(), json.dumps(data)))

    def attempt(self, run_id: str, attempt: Attempt):
        self.event("attempts", run_id, asdict(attempt))
        if attempt.outcome in {Outcome.OK, Outcome.LOW_QUALITY, Outcome.INCOMPLETE}:
            with self.db:
                self.db.execute("""INSERT INTO routes VALUES(?,?,1,?,?,?)
                    ON CONFLICT(key,method) DO UPDATE SET trials=trials+1,
                    successes=successes+excluded.successes,
                    total_ms=total_ms+excluded.total_ms, updated=excluded.updated""", (
                    route_key(attempt.url), attempt.method, int(attempt.outcome == Outcome.OK),
                    attempt.elapsed_ms, time.time()))

    def learned_browser(self, url: str) -> bool:
        rows = {r["method"]: r for r in self.db.execute(
            "SELECT * FROM routes WHERE key=? AND updated>?", (route_key(url), time.time() - 7 * 86400))}
        http, browser = rows.get("http"), rows.get("browser")
        if not http or not browser or http["trials"] < 2 or browser["successes"] < 2:
            return False
        # Every fifth browser use rechecks HTTP, so the learned route can recover from site changes.
        return http["successes"] / http["trials"] < 0.2 and browser["successes"] / browser["trials"] > 0.8 \
            and browser["trials"] % 5 != 0

    def events(self, table: str, run_id: str) -> list[dict]:
        if table not in {"requests", "attempts", "decisions"}:
            raise ValueError("Unknown event table")
        return [json.loads(row[0]) for row in self.db.execute(
            f"SELECT data FROM {table} WHERE run_id=? ORDER BY rowid", (run_id,))]

    def documents(self, run_id: str) -> list[dict]:
        return [json.loads(row[0]) for row in self.db.execute(
            "SELECT document FROM results WHERE run_id=? ORDER BY rowid", (run_id,))]

    def finish(self, run_id: str, status: str, summary: dict):
        with self.db:
            self.db.execute("UPDATE runs SET status=?,finished=?,summary=? WHERE id=?",
                            (status, time.time(), json.dumps(summary), run_id))

    def latest(self):
        return self.db.execute("SELECT * FROM runs ORDER BY started DESC LIMIT 1").fetchone()
