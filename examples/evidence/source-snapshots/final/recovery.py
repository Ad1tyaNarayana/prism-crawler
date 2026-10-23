from .models import Outcome, Verification


def can_recover(outcome: Outcome, assessment: Verification | None) -> bool:
    return outcome == Outcome.LOW_QUALITY or (
        outcome == Outcome.INCOMPLETE and assessment is not None and assessment.recoverable)


def should_render(outcome: Outcome, assessment: Verification | None, needs_js: bool,
                  browser_attempted: bool, model_requests_browser: bool = False) -> bool:
    return not browser_attempted and can_recover(outcome, assessment) and (
        outcome == Outcome.INCOMPLETE or needs_js or model_requests_browser)
