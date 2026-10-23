import pytest

from optimal_crawler.decisions import BLOCK_QUESTION, DecisionEngine, validate_answers
from optimal_crawler.models import Candidate, CrawlConfig


def test_invalid_probabilities_are_rejected():
    bad = {"answers": {"content": {"type": "choice", "choice": "A",
                                  "probabilities": {"A": float("nan"), "B": 0}}}}
    with pytest.raises(ValueError, match="probability"):
        validate_answers(bad, BLOCK_QUESTION)
    bad["answers"]["content"]["probabilities"] = {"A": 0.9, "B": 0.9}
    with pytest.raises(ValueError, match="sum"):
        validate_answers(bad, BLOCK_QUESTION)


@pytest.mark.parametrize("result", [[], {"answers": []}, {"answers": {"content": None}}])
def test_malformed_model_shape_fails_closed(result):
    with pytest.raises(ValueError):
        validate_answers(result, BLOCK_QUESTION)


async def test_parser_advice_requires_both_sides_to_be_confident(monkeypatch):
    candidates = [Candidate("semantic", "Boilerplate " * 30, "Title", 1.0),
                  Candidate("trafilatura", "Real article " * 30, "Title", 0.95)]
    engine = DecisionEngine(CrawlConfig(decision_provider="systemone", decision_mode="assist"))

    async def decision(state, *_):
        good = state.startswith("Real")
        return {"answers": {"content": {"type": "choice", "choice": "A" if good else "B",
                                        "probabilities": {"A": 0.97 if good else 0.01,
                                                          "B": 0.03 if good else 0.99}}}}
    monkeypatch.setattr(engine, "decide", decision)
    assert await engine.select("https://example.org", candidates) is candidates[1]
    shadow = DecisionEngine(CrawlConfig(decision_provider="systemone", decision_mode="shadow"))
    monkeypatch.setattr(shadow, "decide", decision)
    assert await shadow.select("https://example.org", candidates) is candidates[0]


async def test_provider_failure_keeps_deterministic_candidate():
    engine = DecisionEngine(CrawlConfig(decision_provider="systemone", decision_endpoint="http://127.0.0.1:1",
                                        decision_mode="assist"))
    candidates = [Candidate("semantic", "First candidate content " * 20, "Title", 0.9),
                  Candidate("trafilatura", "Second candidate content " * 20, "Title", 0.85)]
    assert await engine.select("https://example.org", candidates) is candidates[0]
