import json

import pytest
from aiohttp import web

from optimal_crawler.crawler import Crawler
from optimal_crawler.extract import Extractor
from optimal_crawler.models import CrawlConfig, FetchResult
from optimal_crawler.verification import ContentVerifier
from tests.test_realworld_regressions import serve


def response(body):
    return FetchResult("https://example.org/report", body=body.encode(),
                       headers={"content-type": "text/html"})


def test_prose_cards_inside_content_navigation_are_not_discarded():
    cards = "".join(f'<li><article><h2>Research programme {n}</h2><p>Explore the independent '
                    f'archive programme number {n}, its public records and measurement details.</p>'
                    '</article></li>' for n in range(3))
    source = response('<main><h1>About the archive</h1><nav aria-label="On this page">'
                      '<a href="#intro">Introduction</a><a href="#people">People</a></nav>'
                      '<p>The public archive offers these research programmes.</p>'
                      f'<nav aria-label="Child pages"><ul>{cards}</ul></nav></main>')
    extraction = Extractor().extract(source)
    ContentVerifier().apply(source, extraction, source.url)
    for n in range(3):
        assert f"Research programme {n}" in extraction.best.text
    assert "Introduction" not in extraction.best.text
    assert extraction.best.verification.accepted
    assert len(extraction.evidence.headings) == 4


@pytest.mark.parametrize("lookup", [
    'document.getElementById("release-table")',
    'document.getElementById(`${prefix}-table`)',
])
def test_empty_region_populated_by_network_script_is_incomplete(lookup):
    source = response('<main><h1>Release catalogue</h1><p>' + "This is readable introduction text. " * 15
                      + '</p><div id="release-table"></div></main><script>'
                      'fetch("/releases.json").then(r=>r.json()).then(data=>{'
                      f'{lookup}.innerHTML=data.html;'
                      '});</script>')
    extraction = Extractor().extract(source)
    ContentVerifier().apply(source, extraction, source.url)
    assert extraction.evidence.deferred_content
    assert not extraction.best.verification.accepted
    assert extraction.best.verification.recoverable


def test_unrelated_analytics_fetch_does_not_make_empty_decorations_incomplete():
    source = response('<main><h1>Published report</h1><p>' + "Complete published findings. " * 15
                      + '</p><div id="ornament"></div></main>'
                      '<script>fetch("/analytics");document.getElementById("outside");</script>'
                      '<aside id="outside"></aside>')
    extraction = Extractor().extract(source)
    ContentVerifier().apply(source, extraction, source.url)
    assert not extraction.evidence.deferred_content
    assert extraction.best.verification.accepted


def test_reading_an_empty_region_for_analytics_does_not_request_rendering():
    source = response('<main><h1>Published report</h1><p>' + "Complete published findings. " * 15
                      + '</p><div id="ornament"></div></main><script>'
                      'fetch("/analytics");document.getElementById("ornament").getBoundingClientRect();'
                      '</script>')
    assert not ContentVerifier().source(source).deferred_content


def test_network_script_bound_target_append_is_deferred_until_populated():
    source = response('<main><h1>Release list</h1><div id="stable-list"></div></main><script>'
                      'fetch("/releases.json").then(r=>r.json()).then(data=>{'
                      'const output = window.document.getElementById(`${prefix}-list`);'
                      'output.append(data.html);});</script>')
    assert ContentVerifier().source(source).deferred_content


async def test_long_prose_does_not_suppress_deferred_table_browser_recovery(tmp_path):
    rows = '<table><tr><th>Platform</th><th>Release</th></tr><tr><td>Local workstation</td>' \
           '<td>Release 42 includes verified instrument calibration records.</td></tr></table>'
    introduction = " ".join(f"Instrument {n} has a published calibration history with independent "
                            "measurement records and a repair log." for n in range(10))

    async def handler(request):
        if request.path == "/robots.txt":
            return web.Response(text="User-agent: *\nAllow: /\n")
        if request.path == "/catalogue.json":
            return web.json_response({"html": rows})
        return web.Response(text='<main><h1>Instrument catalogue</h1><p>'
                            + introduction
                            + '</p><div id="catalogue-table"></div></main><script>'
                            'fetch("/catalogue.json").then(r=>r.json()).then(data=>{'
                            'document.getElementById("catalogue-table").innerHTML=data.html;'
                            '});</script>', content_type="text/html")

    async with serve(handler) as base:
        config = CrawlConfig(db=tmp_path / "catalogue.db", allow_private=True, min_delay=0,
                             discover=False, follow_links=False, timeout=3,
                             browser_timeout=5, render_wait=.2)
        async with Crawler(config) as crawler:
            await crawler.run([base + "/catalogue"])
            doc = crawler.store.documents(crawler.run_id)[0]
    assert doc["outcome"] == "ok", json.dumps(doc)
    assert doc["method"] == "browser"
    assert "Release 42" in doc["text"]
    assert "Local workstation" in doc["text"]


def test_legacy_global_chrome_does_not_pollute_content_without_main_tag():
    menu = ''.join(f'<a href="/{n}">Global navigation destination {n}</a>' for n in range(8))
    body = ('<body><div id="area_header">' + menu + '</div><div id="area_content">'
            '<h1>Space research programme</h1><h2>Open mission records</h2><p>'
            'The institute publishes its original mission observations with calibrated data, '
            'research methods and public documentation for independent review.</p>'
            '<h2>Measurement archive</h2><p>All published observations include their calibration '
            'history, independent analysis and the original mission record.</p></div>'
            '<div id="area_footer">' + menu + '</div></body>')
    source = response(body)
    extraction = Extractor().extract(source)
    ContentVerifier().apply(source, extraction, source.url)
    assert "Global navigation destination" not in extraction.best.text
    assert "Open mission records" in extraction.best.text
    assert extraction.best.verification.accepted


def test_article_header_and_footer_links_are_content():
    links = ''.join(f'<a href="/{n}">Research author {n}</a>' for n in range(4))
    source = response('<article><div id="article_header"><h1>Original research</h1>' + links
                      + '</div><p>The full paper describes the measurement method, '
                      'independent observations and their implications for future research.</p>'
                      '<div class="article-footer">Citation records ' + links + '</div></article>')
    text = Extractor().extract(source).best.text
    assert "Original research" in text
    assert "Research author 3" in text
    assert "Citation records" in text


def test_prose_in_a_header_named_div_is_not_assumed_to_be_a_global_menu():
    source = response('<div id="header"><h1>Published field report</h1><p>'
                      'The expedition publishes original research and a detailed account of its '
                      'field methods. Full observation records follow the introduction.</p></div>'
                      '<div><h2>Observation archive</h2><p>Readers can inspect the '
                      'independently reviewed record and supporting evidence.</p></div>')
    text = Extractor().extract(source).best.text
    assert "Full observation records follow" in text
    assert "independently reviewed record" in text
