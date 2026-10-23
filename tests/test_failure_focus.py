from benchmarks.failure_focus import failure_stage, listing_audit


def listing():
    stories = ''.join(
        f'<li data-shortid="s{i}"><div class="voters">{i + 1}</div>'
        f'<a class="u-url">Distinct story title {i}</a>'
        f'<span class="comments_label"> | <a>{i + 2} comments</a></span></li>'
        for i in range(12)
    )
    output = '\n'.join(f'{i+1}\nDistinct story title {i}\n{i+2} comments' for i in range(12))
    return f'<main><ol>{stories}</ol></main>'.encode(), output


def test_listing_review_uses_source_items_without_inventing_point_keyword():
    body, text = listing()
    result = listing_audit(body, {'outcome': 'ok', 'text': text})
    assert result['usable_listing']
    assert result['covered'] == result['stories'] == 12
    assert not result['source_contains_point']
    assert 'point' not in text


def test_listing_review_rejects_dropped_story_even_when_benchmark_keywords_match():
    body, text = listing()
    result = listing_audit(body, {'outcome': 'ok', 'text': text.replace('Distinct story title 5', '') + '\npoint comment'})
    assert not result['usable_listing']
    assert result['covered'] == 11


def test_listing_review_does_not_accept_navigation_or_wall():
    body, text = listing()
    assert not listing_audit(body, {'outcome': 'ok', 'text': 'Lobsters point comment'})['usable_listing']
    assert not listing_audit(body, {'outcome': 'access_wall', 'text': text})['usable_listing']
    assert not listing_audit(b'<main>point comments</main>', {'outcome': 'ok', 'text': text})['usable_listing']


def test_quality_check_failure_is_not_automatically_reclassified_as_success():
    assert failure_stage({'outcome': 'ok'}, {'passed': False}) == 'quality_review_required'
    assert failure_stage({'outcome': 'ok'}, {'passed': True}) == 'passed'


def test_access_and_identity_failures_remain_distinct():
    assert failure_stage({'outcome': 'access_wall'}, {'passed': False}) == 'access_response'
    document = {'outcome': 'incomplete', 'metadata': {'verification': {'issues': ['target_identifier_changed']}}}
    assert failure_stage(document, {'passed': False}) == 'target_changed'
