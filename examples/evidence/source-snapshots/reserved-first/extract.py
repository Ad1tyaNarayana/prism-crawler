import gzip
import io
import json
import re
import subprocess
import sys
from collections import Counter
from html import unescape
from urllib.parse import urlsplit

import trafilatura
from lxml import etree, html

from .models import Candidate, Extraction, FetchResult
from .urls import canonicalize

WALL_TITLES = re.compile(r"^(?:just a moment|access denied|verify (?:that )?you are human|"
                         r"verifying your browser|making sure you(?:'|’)re not a bot|"
                         r"attention required|client challenge|vercel security checkpoint|"
                         r"sign in to continue|log in to continue)", re.I)
SHELL_TEXT = re.compile(r"^(?:loading[.\s…]*|please (?:enable|turn on) javascript.*|"
                       r"you need to enable javascript.*|javascript (?:is )?required.*)$", re.I | re.S)


def clean(text: str) -> str:
    return "\n".join(line for line in (re.sub(r"\s+", " ", unescape(line)).strip()
                                     for line in text.splitlines()) if line)


def quality(text: str, semantic: bool = False) -> float:
    text = clean(text)
    if len(text) < 25 or SHELL_TEXT.match(text):
        return 0
    letters = sum(c.isalpha() for c in text)
    if letters / max(1, len(text)) < 0.3:
        return 0.15
    words = re.findall(r"\w+", text)
    cjk = sum("\u3040" <= c <= "\u9fff" for c in text)
    if len(words) < 6 and cjk < 16:
        return 0.2
    value = min(1.0, 0.46 + min(len(text) / 800, 0.4) + (0.12 if semantic else 0))
    sentences = [s.strip().lower() for s in re.split(r"[.!?]\s+", text) if len(s.strip()) > 10]
    if len(sentences) >= 6 and len(set(sentences)) / len(sentences) < 0.5:
        value *= 0.45
    return round(value, 4)


def text_of(element) -> str:
    return clean("\n".join(element.itertext()))


def strip_chrome(tree) -> None:
    # Headers inside sections/cards can contain the document's actual title.
    for node in tree.xpath("//script|//style|//noscript|//nav|//footer|//aside|//form|"
                           "//header[@role='banner' or parent::body or "
                           "not(.//h1|.//h2|.//h3|.//h4|.//h5|.//h6)]|"
                           "//*[@hidden]|//*[@aria-hidden='true']"):
        node.drop_tree()


def cms_prose(container) -> str:
    # Undecorated CMS block tables expose layout parameters as ordinary div text.
    cells = container.xpath(".//div[not(*) and not(ancestor::pre or ancestor::code)]")
    grids = sum(bool(re.fullmatch(r"col-(?:xs|sm|md|lg|xl|xxl)-\d{1,2}",
                                 (cell.text or "").strip())) for cell in cells)
    if grids < 4 or not container.xpath(".//div[contains(concat(' ',normalize-space(@class),' '),"
                                       "' section-metadata ')]"):
        return ""
    blocks = container.xpath(".//h1|.//h2|.//h3|.//h4|.//h5|.//h6|.//p|.//li|.//pre|"
                             ".//blockquote|.//div[not(*)]")
    selected = set(blocks)
    return clean("\n".join(text_of(node) for node in blocks
        if not any(parent in selected or 'section-metadata' in parent.get('class', '').split()
                   for parent in node.iterancestors())
        and (node.tag != 'div' or len(text_of(node)) >= 80)))


def access_wall(tree) -> bool:
    titles = tree.xpath("//title/text() | //h1[1]//text()")
    if any(WALL_TITLES.match(clean(title)) for title in titles):
        return True
    visible = " ".join(tree.xpath("//text()[not(ancestor::script or ancestor::style or ancestor::title)]"))
    visible = clean(visible).lower()
    if len(visible) > 6000:
        return False
    verification = any(phrase in visible for phrase in (
        "we need to verify that you're not a robot", "verify you are human to continue",
        "checking your browser before accessing", "verify that you are not a robot to continue"))
    queue = "this page will automatically refresh" in visible and any(
        phrase in visible for phrase in ("as soon as we can handle", "waiting room", "in the queue"))
    return verification or queue


def has_repeated_records(tree) -> bool:
    for parent in tree.iter():
        children = [child for child in parent if isinstance(child.tag, str)]
        if len(children) < 3:
            continue
        groups = Counter((child.tag, child.get("class", "")) for child in children)
        for shape, count in groups.items():
            if count < 3:
                continue
            group = [child for child in children if (child.tag, child.get("class", "")) == shape]
            substantial = [child for child in group if len(child.text_content().strip()) >= 50]
            if len(substantial) < 3:
                continue
            total = sum(len(child.text_content()) for child in substantial)
            links = sum(len(a.text_content()) for child in substantial for a in child.xpath(".//a"))
            if links / max(total, 1) < 0.5:
                return True
    return False


def json_candidates(data, parser: str) -> list[Candidate]:
    candidates: list[Candidate] = []
    pending = [data]
    visited = 0
    while pending and visited < 1000:
        node = pending.pop()
        visited += 1
        if isinstance(node, list):
            pending.extend(node[:100])
        elif isinstance(node, dict):
            title = str(node.get("headline") or node.get("title") or node.get("name") or "")[:300]
            field = next((k for k in ("articleBody", "body", "content", "text", "description")
                          if isinstance(node.get(k), str) and len(node[k]) >= 25), "")
            body = node.get(field, "")
            if body:
                if body.lstrip().startswith(("{", "[")):
                    try:
                        decoded = json.loads(body)
                    except (ValueError, RecursionError):
                        decoded = None
                    if isinstance(decoded, (dict, list)):
                        pending.append(decoded)
                        pending.extend(v for v in node.values() if isinstance(v, (dict, list)))
                        continue
                if "<" in body:
                    try:
                        body = text_of(html.fromstring(body))
                    except (etree.ParserError, ValueError):
                        pass
                body = clean(body)
                score = quality(body, True)
                if score:
                    candidates.append(Candidate(parser, body, title, score,
                                                {"type": node.get("@type", "object"), "field": field}))
            pending.extend(v for v in node.values() if isinstance(v, (dict, list)))
    return candidates


def parse_xml(raw: bytes):
    return etree.fromstring(raw, parser=etree.XMLParser(resolve_entities=False, no_network=True,
                                                       recover=False, huge_tree=False))


def xml_discovery(raw: bytes, url: str, max_bytes: int = 4_000_000) -> tuple[list[str], list[str]]:
    if raw.startswith(b"\x1f\x8b"):
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as compressed:
                raw = compressed.read(max_bytes + 1)
            if len(raw) > max_bytes:
                return [], []
        except (OSError, EOFError):
            return [], []
    try:
        root = parse_xml(raw)
    except (etree.XMLSyntaxError, ValueError):
        return [], []
    root_name = etree.QName(root).localname.lower()
    urls, nested = [], []
    if root_name in {"urlset", "sitemapindex"}:
        for loc in root.xpath("//*[local-name()='loc']/text()")[:5000]:
            value = canonicalize(loc, url)
            if value:
                (nested if root_name == "sitemapindex" else urls).append(value)
    elif root_name in {"rss", "feed", "rdf"}:
        for value in root.xpath("//*[local-name()='item' or local-name()='entry']/*"
                               "[local-name()='link']/text() | //*[local-name()='entry']/*"
                               "[local-name()='link']/@href")[:5000]:
            value = canonicalize(value, url)
            if value:
                urls.append(value)
    return urls, nested


class Extractor:
    def extract(self, result: FetchResult, structured: bool = True) -> Extraction:
        raw = result.text
        content_type = result.headers.get("content-type", "").lower()
        extraction = Extraction()
        if "application/pdf" in content_type or result.body.startswith(b"%PDF-"):
            try:
                parsed = subprocess.run([sys.executable, "-m", "optimal_crawler.pdf_worker"],
                                        input=result.body, capture_output=True, timeout=10, check=False)
                if parsed.returncode:
                    raise ValueError("PDF parser stopped at a resource limit")
                payload = json.loads(parsed.stdout)
                if payload.get("error"):
                    raise ValueError(payload["error"])
                text = clean(payload.pop("text"))
                extraction.candidates = [Candidate("pdf", text, payload.pop("title"), quality(text, True),
                                                   {"pdf": payload, "source_truncated": payload["truncated"]})]
            except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
                extraction.unsupported = True
                extraction.reason = "PDF extraction unavailable: " + str(exc)[:240]
            return extraction
        if "json" in content_type or raw.lstrip().startswith(("{", "[")):
            try:
                extraction.candidates = json_candidates(json.loads(raw), "json")
                extraction.reason = "No supported textual fields in JSON" if not extraction.candidates else ""
            except (ValueError, RecursionError):
                extraction.reason = "Malformed JSON"
            return extraction
        if "xml" in content_type or raw.lstrip().startswith(("<?xml", "<rss", "<feed")):
            try:
                root = parse_xml(result.body)
                entries = root.xpath("//*[local-name()='item' or local-name()='entry']")
                text = clean("\n".join("\n".join(entry.xpath(
                    "./*[local-name()='title' or local-name()='description' or local-name()='summary' "
                    "or local-name()='content' or local-name()='encoded']//text()")) for entry in entries))
                if "<" in text:
                    text = clean(html.fromstring(f"<div>{text}</div>").text_content())
                title = " ".join(root.xpath("./*[local-name()='title']/text() | "
                                           "./*[local-name()='channel']/*[local-name()='title']/text()"))
                if text:
                    extraction.candidates.append(Candidate("feed", text, title, quality(text, True)))
                extraction.links, _ = xml_discovery(result.body, result.url)
            except (etree.XMLSyntaxError, etree.ParserError, ValueError):
                extraction.reason = "Unsupported or malformed XML"
            return extraction
        if content_type and not any(t in content_type for t in ("html", "text/plain")):
            extraction.reason = f"Unsupported content type {content_type}"
            return extraction
        try:
            tree = html.fromstring(raw)
        except (etree.ParserError, ValueError):
            extraction.reason = "Empty or malformed document"
            return extraction
        title = clean(" ".join(tree.xpath("//head/title[1]/text()"))
                      or " ".join(tree.xpath("(//h1)[1]//text()")))[:300]
        if access_wall(tree):
            extraction.wall, extraction.reason = True, "Visible access or authentication wall"
            return extraction
        base = result.url
        base_tags = tree.xpath("//base/@href")
        if base_tags:
            base = canonicalize(base_tags[0], base) or base
        robots = " ".join(tree.xpath("//meta[translate(@name,'ROBOTS','robots')='robots']/@content")) \
            + " " + result.headers.get("x-robots-tag", "")
        if "nofollow" not in robots.lower() and "none" not in robots.lower():
            extraction.links = list(dict.fromkeys(
                u for link in tree.xpath("//a[not(contains(@rel,'nofollow'))]/@href")[:5000]
                if (u := canonicalize(link, base))))
        for link in tree.xpath("//link[contains(@rel,'alternate')]"):
            value = canonicalize(link.get("href", ""), base)
            kind = link.get("type", "").lower()
            if not value:
                continue
            if "json" in kind:
                extraction.alternatives.append(value)
            elif any(t in kind for t in ("rss", "atom")):
                extraction.feeds.append(value)
        scripts = tree.xpath("//script")
        if structured:
            for script in scripts[:100]:
                kind = script.get("type", "").lower()
                if "json" not in kind:
                    continue
                try:
                    data = json.loads(script.text or "")
                except (ValueError, RecursionError):
                    continue
                parser = "jsonld" if "ld+json" in kind else "hydration"
                extraction.candidates.extend(json_candidates(data, parser))
        strip_chrome(tree)
        for node in tree.xpath("//img[@alt] | //math[@alttext]"):
            alternative = node.get("alttext") or node.get("alt", "")
            hint = " ".join((node.get("class", ""), node.get("role", ""))).lower()
            if node.tag == "math" or any(word in hint for word in ("equation", "formula", "math")):
                replacement = html.Element("span")
                replacement.text = alternative
                replacement.tail = node.tail
                node.getparent().replace(node, replacement)
        semantic = tree.xpath("//article|//main|//*[@role='main']")
        body = tree.find("body")
        body = body if body is not None else tree
        container = max(semantic, key=lambda e: len(e.text_content()), default=body)
        prose = cms_prose(container)
        if prose:
            extraction.candidates.append(Candidate("cms_prose", prose, title, quality(prose, True)))
        fallback = text_of(container)
        body_visible = text_of(body)
        if not semantic and len(body_visible) < 800 and any(phrase in body_visible.lower() for phrase in (
                "it appears that you are offline", "a required part of this site couldn",
                "application error: a client-side exception")):
            extraction.candidates.clear()
            extraction.reason = "Application failed to load its content"
            extraction.needs_js = bool(scripts)
            return extraction
        link_density = sum(len(a.text_content()) for a in container.xpath(".//a")) / max(1, len(fallback))
        navigation_only = not semantic and len(fallback) < 200 and link_density > 0.7
        extraction.needs_js = bool(scripts) and (len(fallback) < 100 or bool(SHELL_TEXT.match(fallback)))
        if SHELL_TEXT.match(fallback) or ("loading" in fallback.lower() and len(fallback) < 60):
            fallback = ""
        if fallback:
            containers = sorted(semantic, key=lambda e: len(e.text_content()), reverse=True)[:10] or [body]
            main = [e for e in semantic if e.tag == "main" or e.get("role") == "main"]
            semantic_set = set(semantic)
            independent = [e for e in semantic if not any(p in semantic_set for p in e.iterancestors())]
            if not main and len(independent) > 1:
                containers.insert(0, body)
            structured_body = bool(semantic) or has_repeated_records(body)
            for element in containers:
                content = text_of(element)
                value = 0.2 if navigation_only else quality(content, structured_body)
                extraction.candidates.append(Candidate("semantic", content, title, value,
                                                       {"element": element.tag}))
        try:
            text = trafilatura.extract(raw, url=result.url, include_comments=False, include_tables=True,
                                       include_links=False, favor_precision=True, fast=True)
        except (ValueError, TypeError, etree.LxmlError):
            text = None
        if text and not SHELL_TEXT.match(clean(text)):
            text = clean(text)
            extraction.candidates.append(Candidate("trafilatura", text, title, quality(text, True)))
        if len(body_visible) < 40 and not extraction.candidates:
            extraction.reason = "No main content found"
        unique = {}
        documents = [c for c in extraction.candidates
                     if c.parser in {"semantic", "trafilatura"} and c.quality >= 0.5]
        for candidate in extraction.candidates:
            if candidate.parser in {"jsonld", "hydration"} and candidate.metadata.get("field") != "articleBody" \
                    and any(len(c.text) >= max(len(candidate.text) * 1.5, len(candidate.text) + 200)
                            and c.quality >= candidate.quality - 0.05 for c in documents):
                candidate.quality = round(candidate.quality * 0.85, 4)
            if candidate.text not in unique or candidate.quality > unique[candidate.text].quality:
                unique[candidate.text] = candidate
        extraction.candidates = sorted(unique.values(), key=lambda c: c.quality, reverse=True)
        return extraction


def page_like(url: str) -> bool:
    return not re.search(r"\.(?:css|js|map|ico|zip|woff2?|jpg|png|webp|svg|mp[34])$",
                         urlsplit(url).path, re.I)
