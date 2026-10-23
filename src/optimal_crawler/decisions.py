import asyncio
import math
import os
import time

import aiohttp

from .models import Candidate, CrawlConfig
from .store import Store

ROUTE_QUESTION = {
    "route": {
        "type": "choice",
        "instructions": "Which action can recover the missing main content of this public web page?",
        "criteria": {
            "A": "Run JavaScript in a browser because the HTML is an empty application shell",
            "B": "Use the extracted text because it already contains the page's main content",
            "C": "Stop because the page has no useful content or requires authentication",
        },
    }
}

BLOCK_QUESTION = {
    "content": {
        "type": "choice",
        "instructions": "Classify this extracted website text.",
        "criteria": {
            "A": "Substantive content: article, factual notice, product details, schedule or reference information",
            "B": "Boilerplate: navigation, advertising, login form, access challenge, loading message or cookie consent",
        },
    }
}


def validate_answers(result: dict, questions: dict) -> dict:
    if not isinstance(result, dict):
        raise ValueError("Decision response must be an object")
    answers = result.get("answers", {})
    if not isinstance(answers, dict):
        raise ValueError("Decision answers must be an object")
    for name, question in questions.items():
        answer = answers.get(name)
        if not isinstance(answer, dict) or answer.get("type") != question["type"]:
            raise ValueError(f"Missing or invalid typed answer for {name}")
        if question["type"] == "choice":
            probabilities = answer.get("probabilities", {})
            if not isinstance(probabilities, dict) or set(probabilities) != set(question["criteria"]):
                raise ValueError("Decision options do not match the request")
            if answer.get("choice") not in probabilities:
                raise ValueError("Decision returned an unknown choice")
            values = list(probabilities.values())
            if any(not isinstance(p, (int, float)) or not math.isfinite(p) or not 0 <= p <= 1 for p in values):
                raise ValueError("Invalid decision probability")
            if abs(sum(values) - 1) > 0.025:
                raise ValueError("Decision probabilities do not sum to one")
    return answers


class DecisionEngine:
    def __init__(self, config: CrawlConfig, store: Store | None = None, run_id: str = ""):
        self.config, self.store, self.run_id = config, store, run_id
        self._router = None
        self._lock = asyncio.Lock()
        self.calls = 0
        self.elapsed_ms = 0.0

    def _local(self, state, questions):
        if self._router is None:
            import torch
            from laya import Router

            device = "mps" if torch.backends.mps.is_available() else "cpu"
            self._router = Router(device=device, default="multilingual", max_loaded=2)
        model = None if self.config.laya_model == "auto" else self.config.laya_model
        return self._router.predict(state, questions, model=model, max_len=1024)

    async def decide(self, state, questions: dict, purpose: str, url: str = "") -> dict | None:
        if self.config.decision_provider == "none":
            return None
        async with self._lock:
            start = time.perf_counter()
            error, result = "", None
            try:
                if self.config.decision_provider == "laya":
                    task = asyncio.create_task(asyncio.to_thread(self._local, state, questions))
                    try:
                        result = await asyncio.shield(task)
                    except asyncio.CancelledError:
                        # Native inference cannot be interrupted safely while it owns the model.
                        await task
                        raise
                else:
                    token = os.environ.get("PRISM_DECISION_API_KEY", "")
                    headers = {"Authorization": f"Bearer {token}"} if token else {}
                    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
                        async with session.post(self.config.decision_endpoint.rstrip("/") + "/v1/systemone",
                                                headers=headers, json={"model": self.config.decision_model,
                                                "state": state, "questions": questions}) as response:
                            response.raise_for_status()
                            raw = await response.content.read(262145)
                            if len(raw) > 262144:
                                raise ValueError("Decision response exceeds limit")
                            import json

                            result = json.loads(raw)
                validate_answers(result, questions)
            except (ImportError, RuntimeError, ValueError, OSError, aiohttp.ClientError, TimeoutError) as exc:
                error = f"{type(exc).__name__}: {str(exc)[:240]}"
            elapsed = (time.perf_counter() - start) * 1000
            self.calls += 1
            self.elapsed_ms += elapsed
            if self.store:
                self.store.event("decisions", self.run_id, {
                    "url": url, "purpose": purpose, "provider": self.config.decision_provider,
                    "mode": self.config.decision_mode, "state": state, "questions": questions,
                    "response": result, "error": error, "elapsed_ms": elapsed,
                })
            return None if error else result

    async def select(self, url: str, candidates: list[Candidate]) -> Candidate | None:
        if not candidates:
            return None
        baseline = candidates[0]
        eligible = [c for c in candidates[:3] if c.quality >= 0.5]
        if len(eligible) < 2 or eligible[0].quality - eligible[1].quality > 0.15:
            return baseline
        assessments = []
        for candidate in eligible:
            answer = await self.decide(candidate.text[:1800], BLOCK_QUESTION, "parser_block", url)
            assessments.append(answer["answers"]["content"]["probabilities"] if answer else {})
        if self.config.decision_mode == "assist" \
                and assessments[0].get("B", 0) >= self.config.decision_threshold:
            for candidate, scores in zip(eligible[1:], assessments[1:], strict=True):
                if scores.get("A", 0) >= self.config.decision_threshold:
                    return candidate
        return baseline
