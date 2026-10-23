from benchmarks.access_diagnostics import response_evidence
from optimal_crawler.models import FetchResult


def test_challenge_header_is_explicit_even_with_empty_body():
    result = FetchResult('https://example.org', 403, headers={
        'CF-Mitigated': 'challenge', 'Set-Cookie': 'private',
        'Server': 'cloudflare', 'Authorization': 'private',
    })
    evidence = response_evidence(result)
    assert evidence['kind'] == 'cloudflare_challenge'
    assert evidence['trigger'] == 'unknown'
    assert evidence['headers'] == {'cf-mitigated': 'challenge', 'server': 'cloudflare'}


def test_cloudflare_server_header_does_not_establish_challenge():
    result = FetchResult('https://example.org', 403, b'<title>Forbidden</title>',
                         {'server': 'cloudflare'})
    assert response_evidence(result)['kind'] == 'http_access_response'


def test_article_mentioning_challenges_is_not_a_challenge():
    result = FetchResult('https://example.org', 200,
        b'<title>Browser security</title><p>Cloudflare uses /cdn-cgi/challenge-platform/.</p>')
    assert response_evidence(result)['kind'] == 'unclassified'


def test_saved_template_signals_are_distinct():
    samples = [
        (b'<title>Just a moment...</title><script src="/cdn-cgi/challenge-platform/a"></script>',
         'cloudflare_challenge_template'),
        (b'<title>Smithsonian request verification</title>', 'site_request_verification'),
        (b'<h1>Sorry, you have been blocked</h1><p>Cloudflare</p>', 'cloudflare_block_template'),
    ]
    for body, expected in samples:
        assert response_evidence(FetchResult('https://example.org', 403, body))['kind'] == expected
