from optimal_crawler.extract import Extractor, text_of
from optimal_crawler.models import FetchResult


def extract(body):
    intro = " ".join(f"Observation {n} includes the independently calibrated research record "
                     "and its complete measurement details." for n in range(10))
    source = FetchResult("https://example.org/tutorial", body=(
        '<main><h1>Research tutorial</h1><p>' + intro + '</p>' + body + '</main>').encode(),
        headers={"content-type": "text/html"})
    return Extractor().extract(source).best.text


def test_highlighted_code_keeps_token_adjacency_indentation_and_newlines():
    text = extract('<pre><code><span>def</span> <span>measure</span><span>():</span>\n'
                   '    <span>return</span> <span>samples</span><span>[0]</span>\n'
                   '</code></pre>')
    assert "def measure():\n    return samples[0]" in text
    code = text[text.index("def measure():"):].strip()
    namespace = {"samples": [42]}
    exec(compile(code, "extracted-example", "exec"), namespace)
    assert namespace["measure"]() == 42


def test_highlighted_markup_and_css_keep_delimiters_and_numeric_units():
    text = extract('<pre><code><span>&lt;</span><span>style</span><span>&gt;</span>\n'
                   '  button { font-size: <span>2</span><span>em</span>; }\n'
                   '<span>&lt;/</span><span>style</span><span>&gt;</span></code></pre>')
    assert "<style>\n  button { font-size: 2em; }\n</style>" in text


def test_inline_words_and_entities_match_the_dom_text_once():
    from lxml import html

    node = html.fromstring('<p>Read <em>the docs</em> and call <code>x.y()</code>.'
                           ' Literal text: <code>&amp;lt;tag&amp;gt;</code></p>')
    assert text_of(node) == "Read the docs and call x.y(). Literal text: &lt;tag&gt;"
