"""Final OK/NG/Review decision (FR-2-4, FR-3-2)."""
from ..config import settings
from ..models import Severity, Status
from ..schemas import DefectIn


def severity_level(score: float) -> Severity:
    if score >= 0.7:
        return Severity.HIGH
    if score >= 0.4:
        return Severity.MEDIUM
    return Severity.LOW


def decide(defects: list[DefectIn]) -> Status:
    threshold = settings.confidence_threshold
    sure = threshold + settings.review_margin
    if any(d.confidence >= sure for d in defects):
        return Status.NG
    if any(d.confidence >= threshold for d in defects):
        return Status.REVIEW
    return Status.OK
