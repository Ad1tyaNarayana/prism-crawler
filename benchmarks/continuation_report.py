"""Summarize frozen development evidence without changing historical scoring."""
import csv
import gzip
import hashlib
import html
import json
from collections import Counter
from pathlib import Path

from markdown_it import MarkdownIt

from benchmarks.real50 import runtime_digest
from benchmarks.real50_review import reviewed_checks
from benchmarks.technical_report import functional_checks

ROOT = Path('reports/continuation')
STRATEGIES = ['http', 'browser', 'adaptive']


def read(path):
    return json.loads(Path(path).read_text())


def main():
    before = read(ROOT / 'before/results.json')
    local = read(ROOT / 'local-release/results.json')
    runs = {s: read(ROOT / f'live50-release/after/run-0-{s}.json') for s in STRATEGIES}
    extra = {s: read(ROOT / f'supplemental-release/{s}.json') for s in STRATEGIES}
    digest = runtime_digest()
    for r in [local, *runs.values(), *extra.values()]:
        assert r['runtime_sha256'] == digest, 'Evidence must match the frozen runtime'
    checked_bodies = set()
    for directory, records in [(ROOT / 'live50-release/after', runs.values()),
                               (ROOT / 'supplemental-release', extra.values())]:
        expected = 50 if directory.name == 'after' else 7
        for record in records:
            assert len(record['documents']) == expected
            assert record['summary']['status'] == 'completed'
            assert record['summary']['decision_calls'] == 0
            for capture in record['captures']:
                sha = capture.get('body_sha256')
                path = directory / 'bodies' / f'{sha}.gz'
                if sha and path not in checked_bodies:
                    assert hashlib.sha256(gzip.decompress(path.read_bytes())).hexdigest() == sha
                    checked_bodies.add(path)
    summary = {'runtime_sha256': digest, 'kind': 'development_rerun', 'repetitions': 1,
               'live_attempts': sum(len(r['documents']) for r in [*runs.values(), *extra.values()]),
               'strategies': {}, 'local': local['records'], 'remaining': [], 'examples': [],
               'verified_body_files': len(checked_bodies)}
    rows, details = [], []
    for s, run in runs.items():
        reviewed = reviewed_checks(run)
        functional = functional_checks(run)
        supplemental = extra[s]
        exa = [c for c in supplemental['checks'] if not c['id'].endswith('-pdf')]
        pdf = [c for c in supplemental['checks'] if c['id'].endswith('-pdf')]
        summary['strategies'][s] = {'reviewed_passed': sum(c['passed'] for c in reviewed),
            'total': 50, 'outcomes': dict(Counter(d['outcome'] for d in run['documents'])),
            'requests': run['summary']['requests'], 'browser_pages': run['summary']['browser_pages'],
            'functional': functional, 'exa_passed': sum(c['passed'] for c in exa),
            'pdf_passed': sum(c['passed'] for c in pdf)}
        for check in reviewed:
            rows.append({'strategy': s, **{k: check[k] for k in ['id', 'url', 'passed', 'outcome', 'chars', 'method', 'reason']},
                         'review_issues': '; '.join(check['review_issues'])})
            if s == 'adaptive' and not check['passed']:
                summary['remaining'].append(check)
        for key in ['archive', 'openstax', 'windy', 'ikea', 'nature', 'nhk-ja']:
            c = next(c for c in reviewed if c['id'] == key)
            doc = next(d for d in run['documents'] if d['url'] == c['url'])
            summary['examples'].append({'strategy': s, 'id': key, 'url': c['url'],
                'outcome': doc['outcome'], 'chars': len(doc['text']), 'original_passed': c['passed'],
                'functional': functional.get(key), 'reason': doc['reason'], 'excerpt': doc['text'][:1200]})
    with (ROOT / 'results.csv').open('w') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (ROOT / 'comparison.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    local_rows = {r['strategy']: r for r in local['records']}
    local_before = {r['strategy']: r for r in before['records']}
    lines = ['# Continued first-party crawler build', '',
        'This pass adds local text-PDF extraction and bounded scrolling to our existing crawler engine. '
        'No hosted crawler, paid API or required model is used. HTTP/HTML libraries, pypdf and Chromium are building blocks.', '',
        'The same 50-site development set was rerun once under each of the three strategies, plus five public Exa pages '
        'and two public research PDFs per strategy. These are fresh live results, with frozen source and unchanged checks. '
        'They are not an unseen evaluation or proof of universal coverage. This single repetition is not a performance study.', '',
        '## Fresh live results', '',
        '| Approach | Reviewed checks, same 50 | Exa pages | Public PDFs | Requests for 50 | Chromium pages for 50 |',
        '| --- | --- | --- | --- | --- | --- |']
    for s, g in summary['strategies'].items():
        lines.append(f"| {s} | {g['reviewed_passed']}/50 | {g['exa_passed']}/5 | {g['pdf_passed']}/2 | {g['requests']} | {g['browser_pages']} |")
    lines += ['', 'The original Windy body-marker check remains unchanged. Its separate functional check requires '
        'actual weather values, a successful forecast response and HEAD probe, and no offline banner. '
        'IKEA stays in the denominator when its requested item cannot be recovered.', '', '## Previously failing real examples', '',
        '| Example, adaptive | Fresh outcome | Characters | Supplemental functional check |', '| --- | --- | --- | --- |']
    for e in summary['examples']:
        if e['strategy'] == 'adaptive':
            functional = ('pass' if e['functional']['passed'] else 'fail') if e['functional'] else 'n/a'
            lines.append(f"| {e['id']} | {e['outcome']} | {e['chars']} | {functional} |")
    lines += ['', 'Exa About and Pricing previously failed because the parser selected short fragments. '
        'Those earlier extraction repairs remain in place. The new verifier detects those fragment failures, '
        'and these fresh runs check that the full content still survives. The installed CLI also extracted About and Pricing directly; '
        '[its exported documents](cli-live.jsonl) retain 10,889 and 4,828 characters respectively. '
        'Run `bash scripts/demo-recovery.sh` to repeat that example.', '', '## Failures reproduced and fixed in this pass', '',
        '| Controlled CLI example | Before, all strategies | After HTTP | After browser | After adaptive |',
        '| --- | --- | --- | --- | --- |']
    for path in ['/deferred', '/paper.pdf', '/infinite', '/long.pdf', '/scan.pdf', '/locked.pdf', '/malformed.pdf', '/private/paper.pdf']:
        states = [f"{local_before[s]['checks'][path]['outcome']}" for s in STRATEGIES]
        old = states[0] if len(set(states)) == 1 else ', '.join(states)
        outcomes = [local_rows[s]['checks'][path]['outcome'] for s in STRATEGIES]
        lines.append('| ' + ' | '.join([path, old, *outcomes]) + ' |')
    lines += ['', 'Before scrolling, the deferred page falsely passed as readable content while omitting the later archive paragraph. '
        'Browser-first and adaptive now include that paragraph. HTTP-only reports unfinished content rather than claiming complete extraction. '
        'PDF seeds were previously skipped without any document result. They now have explicit outcomes.', '',
        'Infinite content and PDFs exceeding extraction limits are marked incomplete and do not export partial text as successful content. '
        'Unsupported, incomplete and policy-refusal outcomes are not counted as extraction successes.', '',
        '## What did not work', '',
        'The first PDF implementation failed on this macOS host because setting RLIMIT_DATA was rejected. '
        'That failed run is preserved in after-local. The revised child process uses a CPU limit, a parent wall deadline, '
        'bounded decoding, and a periodic RSS watchdog. Linux additionally uses OS data and address-space limits. '
        'The watchdog is best effort and is not a hard macOS memory ceiling.', '',
        'The first scroll implementation also waited for unrelated background scripts on pages whose initial content already passed verification. The Exa browser development run exposed this cost and was interrupted; its completed HTTP results and partial SQLite state remain in supplemental. Verified initial content now waits for visible content to settle during scrolling, while unfinished pages retain the full dependency wait. A regression with a blocked background script checks that verified content completes before its browser deadline.', '',
        'The first corrected PDF run labelled a truncated repetitive document low_quality because readability took precedence. '
        'The final acceptance order preserves source-limit failures as incomplete before considering readability. '
        'The failed run is preserved in after-local-fixed.', '',
        '## Remaining live failures', '', '| Site, adaptive | Outcome | Reason |', '| --- | --- | --- |']
    for c in summary['remaining']:
        reason = '; '.join(filter(None, [c['reason'], *c['review_issues']])).replace('|', '/')
        if not reason and c['missing']:
            reason = 'Original content markers missing: ' + ', '.join('/'.join(g) for g in c['missing'])
        lines.append(f"| {c['id']} | {c['outcome']} | {reason} |")
    lines += ['', 'No robots denial, unavailable robots file, access wall, login flow or external origin is bypassed. '
        'Nature requires an out-of-scope identity/cookie flow. Wrong-item redirects cannot recover missing original products. '
        'Timeouts remain errors when permitted requests cannot finish. Fresh outcomes can differ from historical outcomes as websites change.', '',
        '## Verification and limits', '',
        'All 81 tests pass. New tests exercise PDF discovery, file-type sniffing, protected and malformed files, page truncation, '
        'deferred content, bounded infinite growth, and robots enforcement on scroll-triggered requests. '
        'The actual installed CLI was driven under all three strategies. Local server receipts show zero robots-disallowed content requests.', '',
        'Scrolling defaults to six steps, configurable with --scroll-steps between 0 and 20. It respects the existing page deadline. '
        'It does not click buttons or cover every nested scroll container, delayed timer or virtualized list. '
        'Adaptive detection uses explicit unfinished-content signals and can miss sites without them.', '',
        'PDF extraction is bounded to 16 MB input, 100 pages, 200,000 characters, a 10-second worker deadline and six CPU seconds. '
        'It extracts existing text and records empty pages as uncertainty. Encrypted, image-only or blank PDFs are unsupported; OCR '
        'is not implemented. The pypdf documentation explains [text-layer and extraction limits](https://pypdf.readthedocs.io/en/6.21.1/user/extract-text.html).', '',
        'Fresh live scores are not attributed solely to this change. This is a repeated development set, the network is variable, '
        'and there is only one live repetition. No speedup or model-quality claim is made.', '',
        '## Reproduce and inspect', '',
        'From /Users/aditya/Me/optimal-crawler:', '', '```sh',
        '.venv/bin/python -m pytest -q',
        '.venv/bin/python -m benchmarks.continuation_local --output reports/continuation/new-local',
        '.venv/bin/python -m benchmarks.real50 --phase after --repetitions 1 \\',
        '  --output-root reports/continuation/new-live50 --profile benchmarks/technical-profile.json',
        '.venv/bin/prism crawl https://arxiv.org/pdf/1706.03762 --max-pages 1 \\',
        '  --no-discover --no-follow-links --db data/paper.db', '```', '',
        'Use fresh output paths. Existing evidence is preserved. The live runs retain request telemetry, extracted text, '
        'compressed response bodies and a source snapshot. Local request telemetry is not an independent live server receipt.', '',
        f"Runtime SHA-256: `{digest}`.", '',
        'Evidence: [comparison](comparison.json), [all 150 site outcomes](results.csv), '
        '[local before](before/results.json), [local final](local-release/results.json), '
        '[live phase](live50-release/after/phase.json), [supplemental phase](supplemental-release/phase.json).', '']
    markdown = '\n'.join(lines)
    (ROOT / 'README.md').write_text(markdown)
    # Rendering raw source excerpts requires escaping, even though the report is local.
    for e in summary['examples']:
        if e['strategy'] == 'adaptive':
            details.append('<details><summary>' + html.escape(e['id']) + '</summary><p>' +
                           html.escape(e['reason'] or e['outcome']) + '</p><pre>' +
                           html.escape(e['excerpt'] or 'No successful content exported.') + '</pre></details>')
    page = '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">' \
        '<title>Prism continued build evidence</title><style>body{font:16px/1.6 system-ui;max-width:1100px;margin:40px auto;padding:0 24px;color:#18232d;background:#fbfcfd}table{border-collapse:collapse;width:100%;display:block;overflow:auto}th,td{border:1px solid #cbd5df;padding:8px 12px;text-align:left}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#eef2f5;padding:18px}a{color:#075db2}details{padding:12px;border-bottom:1px solid #cbd5df}</style><main>' \
        + MarkdownIt('commonmark', {'html': False}).render(markdown) + '<h2>Extracted examples</h2>' + ''.join(details) + '</main></html>'
    (ROOT / 'index.html').write_text(page)
    Path('docs/continued-build.md').write_text(markdown.replace('](comparison.json)', '](../reports/continuation/comparison.json)')
        .replace('](results.csv)', '](../reports/continuation/results.csv)')
        .replace('](cli-live.jsonl)', '](../reports/continuation/cli-live.jsonl)')
        .replace('](before/', '](../reports/continuation/before/')
        .replace('](local-release/', '](../reports/continuation/local-release/')
        .replace('](live50-release/', '](../reports/continuation/live50-release/')
        .replace('](supplemental-release/', '](../reports/continuation/supplemental-release/'))
    manifest = {'runtime_sha256': digest, 'artifacts': {}}
    for p in sorted(ROOT.rglob('*')):
        if p.is_file() and p != ROOT / 'manifest.json' and (p.suffix in {'.json', '.jsonl', '.csv', '.md', '.html', '.py', '.gz'}):
            manifest['artifacts'][str(p.relative_to(ROOT))] = hashlib.sha256(p.read_bytes()).hexdigest()
    (ROOT / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({k: v for k, v in summary.items() if k in ['runtime_sha256', 'live_attempts', 'strategies']}, indent=2))


if __name__ == '__main__':
    main()
