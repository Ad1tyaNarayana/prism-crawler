"""Bounded document-format decoding in a disposable process, without PDF actions."""
import io
import json
import logging
import resource
import signal
import sys

MAX_INPUT = 16_000_000
MAX_PAGES = 100
MAX_TEXT = 200_000


def parse(raw):
    from pypdf import PdfReader, filters

    for name in ('ZLIB_MAX_OUTPUT_LENGTH', 'LZW_MAX_OUTPUT_LENGTH', 'RUN_LENGTH_MAX_OUTPUT_LENGTH',
                 'MAX_ARRAY_BASED_STREAM_OUTPUT_LENGTH', 'MAX_DECLARED_STREAM_LENGTH'):
        setattr(filters, name, MAX_INPUT)
    reader = PdfReader(io.BytesIO(raw), strict=True)
    if reader.is_encrypted:
        raise ValueError('Encrypted PDF requires operator-provided access; no password attempted')
    pages = len(reader.pages)
    text, size, empty_pages, extracted = [], 0, [], 0
    truncated = pages > MAX_PAGES
    for index in range(min(pages, MAX_PAGES)):
        value = reader.pages[index].extract_text() or ''
        extracted += 1
        if not value.strip():
            empty_pages.append(index + 1)
        remaining = MAX_TEXT - size
        if len(value) > remaining:
            truncated = True
        text.append(value[:remaining])
        size += len(value[:remaining])
        if size >= MAX_TEXT:
            truncated = truncated or index + 1 < pages
            break
    content = '\n\n'.join(text).strip()
    if not content:
        raise ValueError('No extractable text layer; image-only or blank PDF needs OCR')
    return {'text': content, 'title': str((reader.metadata or {}).get('/Title') or '')[:300],
            'pages': pages, 'extracted_pages': extracted, 'empty_pages': empty_pages,
            'truncated': truncated or bool(empty_pages),
            'limits': {'input_bytes': MAX_INPUT, 'pages': MAX_PAGES, 'characters': MAX_TEXT,
                       'worker_seconds': 10, 'cpu_seconds': 6, 'rss_watchdog_bytes': 536870912}}


def check_memory(*_):
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_bytes = peak if sys.platform == 'darwin' else peak * 1024
    if peak_bytes > 512 * 1024 * 1024:
        raise MemoryError('PDF worker exceeded RSS watchdog limit')


def main():
    logging.disable(logging.CRITICAL)
    resource.setrlimit(resource.RLIMIT_CPU, (6, 6))
    if sys.platform.startswith('linux'):
        resource.setrlimit(resource.RLIMIT_DATA, (512 * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_AS, (1024 * 1024 * 1024,) * 2)
    signal.signal(signal.SIGALRM, check_memory)
    signal.setitimer(signal.ITIMER_REAL, 0.05, 0.05)
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise ValueError('PDF input exceeds parser byte limit')
        result = parse(raw)
    except Exception as exc:
        result = {'error': f'{type(exc).__name__}: {str(exc)[:200]}'}
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
    sys.stdout.write(json.dumps(result))


if __name__ == '__main__':
    main()
