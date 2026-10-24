import re
import unicodedata
from dataclasses import asdict
from urllib.parse import unquote, urlsplit

from lxml import etree, html

from .extract import strip_chrome, text_of
from .models import (
    Candidate,
    Extraction,
    FetchResult,
    SourceEvidence,
    Verification,
    VerificationIssue,
)

MAX_HEADINGS = 64
MAX_BLOCKS = 128
MAX_TEXT = 200_000
VERIFIER_VERSION = "source-evidence-v7"


def scripted_content_targets(tree) -> list[re.Pattern]:
    """Recognize a bounded inline fetch + target-write hint without evaluating JS.

    This deliberately handles simple ID lookups, including prefix templates. It
    is a routing hint, not a JavaScript interpreter or a completeness proof.
    """
    targets = []
    lookup = re.compile(r"(?:window\.)?document\.getElementById\(\s*(['\"`])([^\n]{1,160}?)\1\s*\)")
    network = re.compile(r"\bfetch\s*\(|\bXMLHttpRequest\b|\$\.(?:ajax|getJSON)\s*\(")
    write = r"\.(?:(?:innerHTML|innerText|textContent)\s*=|(?:append|appendChild|replaceChildren|insertAdjacentHTML)\s*\()"
    variable = re.compile(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*$")
    template = re.compile(r"\$\{[A-Za-z_$][\w$.]*\}")
    for script in tree.xpath("//script[not(@src)]")[:100]:
        if script.get("type", "").lower() not in {"", "module", "text/javascript", "application/javascript"}:
            continue
        code = (script.text or "")[:MAX_TEXT]
        if not network.search(code):
            continue
        for match in lookup.finditer(code):
            bound = variable.search(code[max(0, match.start() - 160):match.start()])
            direct_write = re.match(r"\s*" + write, code[match.end():])
            bound_write = bound and re.search(r"(?<![\w$])" + re.escape(bound[1]) + write, code)
            if not (direct_write or bound_write):
                continue
            identifier = match[2]
            literal = template.sub("", identifier)
            if "${" in literal or len(literal) < 3:
                continue
            pieces, start = [], 0
            for part in template.finditer(identifier):
                pieces.extend((re.escape(identifier[start:part.start()]), r"[\w:.-]{1,80}"))
                start = part.end()
            pieces.append(re.escape(identifier[start:]))
            targets.append(re.compile("".join(pieces)))
            if len(targets) >= 64:
                return targets
    return targets


def normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def spaced_tokens(text: str) -> str:
    return " ".join(re.findall(r"\w+", normalize(text)))


def contains_block(block: str, candidate: str) -> bool:
    words = spaced_tokens(block).split()
    if len(words) < 12:
        return spaced_tokens(block) in candidate
    starts = (0, (len(words) - 6) // 2, len(words) - 6)
    return sum(" ".join(words[start:start + 6]) in candidate for start in starts) >= 2


def lost_identifier(requested: str, final: str) -> bool:
    path = unquote(urlsplit(requested).path).rstrip("/")
    if path == unquote(urlsplit(final).path).rstrip("/"):
        return False
    segment = path.rsplit("/", 1)[-1]
    identifiers = re.findall(r"(?:^|[-_])(\d{5,})(?=$|[-_.])", segment)
    return any(identifier not in unquote(final) for identifier in identifiers)


class ContentVerifier:
    def source(self, result: FetchResult) -> SourceEvidence:
        kind = result.headers.get("content-type", "").lower()
        raw = result.text
        if (kind and "html" not in kind) or raw.lstrip().startswith(("{", "[", "<?xml", "<rss", "<feed")):
            return SourceEvidence(scope="structured")
        try:
            tree = html.fromstring(raw)
        except (etree.ParserError, ValueError):
            return SourceEvidence()
        executable = any((script.get("type", "").lower() in {
            "", "module", "text/javascript", "application/javascript"})
            and (script.get("src") or (script.text or "").strip()) for script in tree.xpath("//script"))
        targets = scripted_content_targets(tree)
        strip_chrome(tree)
        mains = tree.xpath("//main|//*[@role='main']")
        articles = tree.xpath("//article")
        body = tree.find("body")
        body = body if body is not None else tree
        if mains:
            root, scope = max(mains, key=lambda element: len(element.text_content())), "main"
        elif len(articles) == 1:
            root, scope = articles[0], "article"
        else:
            root, scope = body, "body"
        visible = text_of(root)
        headings = list(dict.fromkeys(normalize(text_of(node)) for node in
                                     root.xpath(".//h1|.//h2|.//h3|.//h4|.//h5|.//h6")
                                     if len(text_of(node)) >= 5))
        nodes = root.xpath(".//p|.//li|.//dd|.//tr|.//pre")
        node_count = len(nodes)
        if len(nodes) > MAX_BLOCKS:
            nodes = [nodes[index * (len(nodes) - 1) // (MAX_BLOCKS - 1)] for index in range(MAX_BLOCKS)]
        blocks = list(dict.fromkeys(normalize(text_of(node))[:1200] for node in nodes
                                   if len(text_of(node)) >= 80))
        loading = executable and len(visible) < 300 and bool(re.search(
            r"\bloading\b|please enable javascript|you need to enable javascript", visible, re.I))
        empty_target = any(node.tag in {"div", "section", "table", "ul", "ol"}
                           and node.get("id") and not text_of(node)
                           and any(target.fullmatch(node.get("id")) for target in targets)
                           for node in root.iter()) if targets else False
        deferred = bool(executable and (empty_target or root.get("aria-busy") == "true"
                        or root.xpath(".//*[@aria-busy='true']")
                        or re.search(r"\bloading (?:more|additional)\b", visible, re.I)))
        return SourceEvidence(scope, tuple(headings[:MAX_HEADINGS]), tuple(blocks), bool(loading),
                              len(headings) > MAX_HEADINGS or node_count > MAX_BLOCKS or len(visible) > MAX_TEXT,
                              deferred)

    def assess(self, requested: str, result: FetchResult, candidate: Candidate,
               evidence: SourceEvidence) -> Verification:
        issues, checks = [], []
        missing, checked, covered = (), 0, 0
        if lost_identifier(requested, result.url):
            issues.append(VerificationIssue.TARGET_IDENTIFIER)
        checks.append("target_identifier")
        if evidence.deferred_content:
            checks.append("deferred_content")
            issues.append(VerificationIssue.DEFERRED_CONTENT)
        if candidate.metadata.get("source_truncated") or result.metadata.get("browser", {}).get("scroll", {}).get("limit_reached"):
            checks.append("source_limits")
            issues.append(VerificationIssue.SOURCE_TRUNCATED)
        if evidence.loading_shell:
            checks.append("loading_shell")
            if candidate.metadata.get("field") != "articleBody":
                issues.append(VerificationIssue.LOADING_SHELL)
        text = candidate.title + "\n" + candidate.text
        limited = evidence.limited or len(text) > MAX_TEXT
        if len(text) <= MAX_TEXT:
            tokens = spaced_tokens(text)
            if len(evidence.headings) >= 3:
                checks.append("main_headings")
                missing = tuple(h for h in evidence.headings if spaced_tokens(h) not in tokens)
                if len(missing) >= 2 and len(missing) / len(evidence.headings) > 0.4:
                    issues.append(VerificationIssue.MISSING_SECTIONS)
            if len(evidence.blocks) >= 3:
                checks.append("source_blocks")
                checked = len(evidence.blocks)
                covered = sum(contains_block(block, tokens) for block in evidence.blocks)
                if covered / checked < 0.6:
                    issues.append(VerificationIssue.MISSING_CONTENT)
        return Verification(tuple(issues), tuple(checks), missing, checked, covered, evidence.scope, limited)

    def apply(self, result: FetchResult, extraction: Extraction, requested: str,
              reference: SourceEvidence | None = None) -> None:
        evidence = reference or self.source(result)
        extraction.evidence = evidence
        for candidate in extraction.candidates:
            candidate.verification = self.assess(requested, result, candidate, evidence)

    @staticmethod
    def metadata(assessment: Verification) -> dict:
        return {**asdict(assessment), "version": VERIFIER_VERSION, "accepted": assessment.accepted,
                "meaning": "No detected gap in the implemented checks; completeness is not guaranteed"}
