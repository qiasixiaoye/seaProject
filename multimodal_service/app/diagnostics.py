from __future__ import annotations

import statistics
from typing import Any


def score_diagnostics(scores: list[float]) -> dict[str, Any]:
    """Describe one result list without claiming absolute relevance calibration."""
    if not scores:
        return {"calibration": "unavailable", "count": 0}
    median = statistics.median(scores)
    mean = statistics.fmean(scores)
    std = statistics.pstdev(scores) if len(scores) > 1 else 0.0
    return {
        "calibration": "unvalidated_relative_only",
        "count": len(scores),
        "top_score": round(scores[0], 6),
        "mean_score": round(mean, 6),
        "median_score": round(median, 6),
        "std_score": round(std, 6),
        "top_margin": round(scores[0] - scores[1], 6) if len(scores) > 1 else None,
        "top_to_median": round(scores[0] - median, 6),
        "note": "Scores are diagnostic only; no absolute relevance threshold has been calibrated.",
    }
