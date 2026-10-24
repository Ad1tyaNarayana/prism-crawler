"""Source-reference scoring, independent of Prism's extraction/verifier."""

import re
import unicodedata
from collections import Counter


def tokens(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", " ", text)
    # Keep CJK characters separate; whitespace word splitting breaks Japanese references.
    return re.findall(r"[a-z0-9_À-ʯ]+|[\u3040-\u9fff\uac00-\ud7af]", text)


def shingles(values, width=3):
    return set(tuple(values[i : i + width]) for i in range(max(0, len(values) - width + 1)))


def coverage(block, output_shingles, output_tokens):
    values = tokens(block)
    if len(values) < 3:
        return float(" ".join(values) in " ".join(output_tokens))
    chunks = shingles(values)
    return len(chunks & output_shingles) / max(1, len(chunks))


def score(text, gold):
    values = tokens(text)
    expected = tokens("\n".join(b["text"] for b in gold["blocks"]))
    actual_count, expected_count = Counter(values), Counter(expected)
    overlap = sum((actual_count & expected_count).values())
    precision = overlap / max(1, len(values))
    recall = overlap / max(1, len(expected))
    chunks = shingles(values)
    blocks = [(coverage(b["text"], chunks, values), len(tokens(b["text"]))) for b in gold["blocks"]]
    block_recall = sum(weight for match, weight in blocks if match >= 0.8) / max(
        1, sum(w for _, w in blocks)
    )
    headings = gold.get("headings", [])
    heading_recall = (
        sum(coverage(h, chunks, values) >= 0.8 for h in headings) / max(1, len(headings))
        if headings
        else None
    )
    code = gold.get("code", [])
    code_recall = (
        sum(coverage(c, chunks, values) >= 0.9 for c in code) / len(code) if code else None
    )
    negatives = [(coverage(b, chunks, values), len(tokens(b))) for b in gold.get("boilerplate", [])]
    contamination = min(
        1, sum(weight for match, weight in negatives if match >= 0.9) / max(1, len(values))
    )
    result = {
        "source_token_precision": precision,
        "source_token_recall": recall,
        "source_token_f1": 2 * precision * recall / max(1e-12, precision + recall),
        "weighted_block_recall": block_recall,
        "heading_recall": heading_recall,
        "code_block_recall": code_recall,
        "matched_boilerplate_token_ratio": contamination,
        "reference_tokens": len(expected),
        "output_tokens": len(values),
        "blocks_recovered": sum(match >= 0.8 for match, _ in blocks),
        "reference_blocks": len(blocks),
    }
    result["quality_pass"] = (
        precision >= 0.8
        and recall >= 0.8
        and block_recall >= 0.75
        and contamination <= 0.1
        and (heading_recall is None or len(headings) < 3 or heading_recall >= 0.7)
    )
    return result
