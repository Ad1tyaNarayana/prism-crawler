import json
from pathlib import Path

from .real50 import normalize


def reviewed_checks(record):
    contract = json.loads(Path("benchmarks/real50-review.json").read_text())
    documents = {document["url"]: document for document in record["documents"]}
    checked = []
    for check in record["checks"]:
        doc = documents.get(check["url"], {})
        rule = contract["checks"].get(check["id"], {})
        issues = []
        text = doc.get("text", "")
        if rule.get("reject_serialized_json"):
            try:
                if isinstance(json.loads(text), (dict, list)):
                    issues.append("Serialized data instead of readable content")
            except ValueError:
                pass
        if rule.get("min_chars", 0) > len(text):
            issues.append("Incomplete course description")
        for group in rule.get("extra_groups", []):
            if not any(normalize(marker) in normalize(text) for marker in group):
                issues.append("Missing required detail: " + " / ".join(group))
        if rule.get("final_url_contains", "") not in doc.get("final_url", ""):
            issues.append("Redirected to a different target")
        checked.append({**check, "topic_passed": check["passed"],
                        "passed": check["passed"] and not issues, "review_issues": issues,
                        "warning": contract["warnings"].get(check["id"], "")})
    return checked


def main():
    for phase in ("baseline", "after"):
        path = Path("reports/real50") / phase
        for file in sorted(path.glob("run-*.json")):
            checks = reviewed_checks(json.loads(file.read_text()))
            print(phase, file.name, sum(c["passed"] for c in checks), "usable / 50;")


if __name__ == "__main__":
    main()
