"""Evidence-only response classification; never changes access or retry decisions."""

from lxml import etree, html

SAFE_HEADERS = frozenset({
    'content-type', 'location', 'retry-after', 'server', 'cf-mitigated',
    'cf-cache-status', 'x-cache', 'via',
})


def response_evidence(result):
    headers = {k.lower(): v for k, v in result.headers.items()}
    raw = result.text[:512_000]
    title = ''
    try:
        tree = html.fromstring(raw)
        title = ' '.join(tree.xpath('string(//title)').split())[:160]
    except (etree.ParserError, ValueError):
        pass
    lowered = raw.lower()
    markers = []
    if headers.get('cf-mitigated', '').lower() == 'challenge':
        markers.append('cf-mitigated: challenge')
        kind = 'cloudflare_challenge'
    elif (title.lower().startswith('just a moment') and
          '/cdn-cgi/challenge-platform/' in lowered):
        markers.append('challenge title plus Cloudflare challenge-platform URL')
        kind = 'cloudflare_challenge_template'
    elif (result.status == 403 and 'cloudflare' in lowered and
          'sorry, you have been blocked' in lowered):
        markers.append('Cloudflare block template')
        kind = 'cloudflare_block_template'
    elif result.status == 403 and title.lower() == 'smithsonian request verification':
        markers.append('Smithsonian request-verification title')
        kind = 'site_request_verification'
    elif result.status in {401, 403, 429}:
        kind = 'http_access_response'
    else:
        kind = 'unclassified'
    return {
        'kind': kind, 'title': title, 'markers': markers,
        'headers': {k: v for k, v in headers.items() if k in SAFE_HEADERS},
        'trigger': 'unknown',
        'meaning': 'Classifies the received response; does not identify the server rule or why another client succeeds.',
    }
