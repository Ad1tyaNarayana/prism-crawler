import gzip
import time

import pytest

from benchmarks.fixture import ARTICLE, html
from optimal_crawler.extract import Extractor, xml_discovery
from optimal_crawler.models import CrawlConfig, Document, FetchResult, Outcome
from optimal_crawler.network import retry_after
from optimal_crawler.store import Store
from optimal_crawler.urls import canonicalize, is_public_ip


def test_url_identity_preserves_semantics():
    assert canonicalize("HTTPS://Example.COM:443/a?b=2&a=1&a=3#frag") == "https://example.com/a?b=2&a=1&a=3"
    assert canonicalize("/a?utm_source=x&x=1", "https://example.com") == "https://example.com/a?x=1"
    assert canonicalize("https://user:secret@example.com/") is None
    assert canonicalize("javascript:alert(1)") is None
    assert canonicalize("http://[::1]:8000/") == "http://[::1]:8000/"
    assert not is_public_ip("127.0.0.1")
    assert not is_public_ip("::ffff:127.0.0.1")
    assert not is_public_ip("169.254.169.254")
    assert is_public_ip("8.8.8.8")


def test_extract_source_text_without_navigation():
    result = FetchResult("https://example.com", body=html(f"<article><p>{ARTICLE}</p></article>").encode(),
                         headers={"content-type": "text/html"})
    extraction = Extractor().extract(result)
    assert "violet comets" in extraction.best.text
    assert "BOILERPLATE_SENTINEL" not in extraction.best.text
    assert extraction.best.quality >= 0.5


def test_xml_entities_cannot_read_files():
    raw = b'<!DOCTYPE x [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><urlset><url><loc>&xxe;</loc></url></urlset>'
    assert xml_discovery(raw, "https://example.com")[0] == []


def test_gzip_sitemap_is_bounded():
    xml = b"<urlset><url><loc>https://example.com/article</loc></url></urlset>"
    assert xml_discovery(gzip.compress(xml), "https://example.com")[0] == ["https://example.com/article"]
    assert xml_discovery(gzip.compress(b"x" * 10000), "https://example.com", max_bytes=100) == ([], [])


def test_nonfinite_runtime_budget_is_invalid():
    with pytest.raises(ValueError, match="finite"):
        CrawlConfig(run_timeout=float("nan"))


def test_conditional_schedule_and_duplicate_persistence(tmp_path):
    config = CrawlConfig(db=tmp_path / "test.db")
    store = Store(config.db)
    run = store.start(config, ["https://example.com/a"])
    doc = Document("https://example.com/a", "https://example.com/a", Outcome.OK, "http", text=ARTICLE)
    store.record(run, doc, config, etag='"v1"')
    first = store.previous(doc.url)["interval"]
    store.record(run, doc, config, etag='"v1"')
    assert store.previous(doc.url)["interval"] > first
    doc.text = "A completely different document about the ocean."
    store.record(run, doc, config, etag='"v2"')
    assert store.previous(doc.url)["interval"] < first
    copy = Document("https://example.com/b", "https://example.com/b", Outcome.OK, "http", text=doc.text)
    store.record(run, copy, config)
    assert copy.duplicate_of == doc.url
    assert store.due(time.time() + 40 * 86400) == [doc.url, copy.url]
    store.close()


def test_resume_recovers_leases(tmp_path):
    config = CrawlConfig(db=tmp_path / "test.db")
    store = Store(config.db)
    seeds = ["https://example.com/"]
    run = store.start(config, seeds)
    store.enqueue(run, seeds[0], 0, 10)
    assert store.claim(run, set())["url"] == seeds[0]
    store.finish(run, "interrupted", {})
    store.close()
    store = Store(config.db)
    assert store.start(config, seeds, resume=True) == run
    assert store.claim(run, set())["url"] == seeds[0]
    store.close()


def test_retry_after_supports_seconds_and_invalid_dates():
    assert retry_after("0.5") == 0.5
    assert retry_after("yesterday") == 0


def test_navigation_only_shell_is_not_content():
    raw = "<html><head><title>Daily reports</title></head><body><h1><a href='/'>Daily reports</a></h1>" \
        "<a href='/login'>Login</a><script src='/app.js'></script></body></html>"
    extraction = Extractor().extract(FetchResult("https://example.com", body=raw.encode(),
                                                headers={"content-type": "text/html"}))
    assert extraction.best.quality < 0.5
    assert extraction.needs_js


def test_repeated_records_are_not_replaced_by_short_intro():
    records = "".join(f"<div class='record'><h2>Country {i}</h2><p>The capital is City {i}. "
                      f"Population: {i * 1000}. Area: {i * 500} square kilometers.</p></div>" for i in range(1, 10))
    raw = html("<p>A directory of countries with geographic and demographic information.</p><section>" + records + "</section>")
    extraction = Extractor().extract(FetchResult("https://example.com", body=raw.encode(),
                                                headers={"content-type": "text/html"}))
    assert all(f"Country {i}" in extraction.best.text for i in range(1, 10))


def test_long_html_comments_are_not_records():
    comments = "".join(f"<!-- This is a long deployment note with no user-visible content {i} -->" for i in range(4))
    raw = html(f"<div>{comments}<p>{ARTICLE}</p></div>")
    extraction = Extractor().extract(FetchResult("https://example.com", body=raw.encode(),
                                                headers={"content-type": "text/html"}))
    assert "forty seven spectra" in extraction.best.text


def test_repetitive_promotion_does_not_outrank_the_article():
    noise = "Subscribe for the latest research. Trending stories. Popular categories. " \
        "Sign in. Share on social media. Join our community. "
    raw = html(f"<main><p>{noise * 7}</p></main><article><p>{ARTICLE}</p></article>")
    extraction = Extractor().extract(FetchResult("https://example.com", body=raw.encode(),
                                                headers={"content-type": "text/html"}))
    assert "forty seven spectra" in extraction.best.text
    assert "Subscribe" not in extraction.best.text
