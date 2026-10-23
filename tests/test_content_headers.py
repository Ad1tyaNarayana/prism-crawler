from optimal_crawler.extract import Extractor
from optimal_crawler.models import FetchResult
from optimal_crawler.verification import ContentVerifier


def test_section_headers_and_linked_card_titles_survive():
    cards = ''.join(f'<a href="/{n}"><figure><header><h3>Research mission number {n}</h3>'
                    '</header></figure>Read</a>' for n in range(5))
    result = FetchResult('https://example.org/', body=(
        '<body><header role="banner">Site navigation</header><section>'
        '<header><h1>Research missions</h1></header>' + cards + '</section></body>').encode(),
        headers={'content-type': 'text/html'})
    extraction = Extractor().extract(result)
    for n in range(5):
        assert f'Research mission number {n}' in extraction.best.text
    assert 'Site navigation' not in extraction.best.text
    evidence = ContentVerifier().source(result)
    assert len(evidence.headings) == 6


def test_cms_layout_cells_do_not_become_article_text():
    cells = ''.join(f'<div><div>col-{size}-12</div></div>' for size in ['xs', 'sm', 'md', 'lg'])
    paragraphs = ''.join(f'<h2>Chapter {n}</h2><p>Observed record {n} contains the complete '
                         'account of this experiment and the published measurement details.</p>'
                         for n in range(4))
    body = ('<main><div class="columns">' + cells + '</div>' + paragraphs +
            '<div class="section-metadata"><div>padding-spacing</div><div>false</div></div></main>')
    result = FetchResult('https://example.org/', body=body.encode(), headers={'content-type': 'text/html'})
    extraction = Extractor().extract(result)
    ContentVerifier().apply(result, extraction, result.url)
    assert 'col-xs-12' not in extraction.best.text
    assert 'padding-spacing' not in extraction.best.text
    assert 'Observed record 3' in extraction.best.text
    assert extraction.best.verification.accepted


def test_grid_tokens_in_code_documentation_remain_content():
    body = '<main><h1>Grid example</h1><pre>col-xs-12 col-sm-12 col-md-12 col-lg-12</pre></main>'
    result = FetchResult('https://example.org/', body=body.encode(), headers={'content-type': 'text/html'})
    assert 'col-xs-12' in Extractor().extract(result).best.text
