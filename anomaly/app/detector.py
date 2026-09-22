"""Hybrid MAD + robust z-score + EWMA anomaly detector.

Score formula (hybrid-mad-ewma-v2)
---------------------------------
Given a protected baseline series B and observation x:

  median = median(B)
  MAD    = median(|B_i - median|)
  σ      = max(1.4826 * MAD, ε)     # ε = per-KPI mad_epsilon floor

  z_raw      = (x - median) / σ
  robust_z   = direction_score(z_raw)   # positive => worse for this KPI

  ewma_prev  = previous EWMA (or median on first sample)
  ewma_value = α * x + (1 - α) * ewma_prev
  ewma_raw   = (x - ewma_prev) / σ
  ewma_score = direction_score(ewma_raw)

  combined_score = max(robust_z, ewma_score)

  is_candidate = (robust_z > 0) and (combined_score >= threshold)

Component scales are dimensionless robust-z / EWMA-z units (multiples of σ).
Using max() ensures a strong robust-z or EWMA signal is not diluted by a weak
companion component. Candidates still require robust_z > 0 so a pure EWMA
transient against a stable level cannot open alone without level deviation.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Sequence


DETECTOR_VERSION = "hybrid-mad-ewma-v2"


@dataclass
class DetectionResult:
    observed: float
    baseline_median: float
    baseline_dispersion: float
    robust_z: float
    ewma_score: float
    combined_score: float
    direction: str
    is_candidate: bool
    ewma_value: float


def median(values: Sequence[float]) -> float:
    return float(statistics.median(values))


def mad(values: Sequence[float], center: float | None = None) -> float:
    if not values:
        return 0.0
    mid = center if center is not None else median(values)
    return float(statistics.median([abs(item - mid) for item in values]))


def dispersion(values: Sequence[float], epsilon: float) -> tuple[float, float]:
    """Return (median, sigma_hat) with MAD floor."""
    mid = median(values)
    raw = 1.4826 * mad(values, mid)
    return mid, max(raw, epsilon)


def direction_score(raw: float, direction: str) -> float:
    """Positive score means anomalous in the KPI's worse direction."""
    if direction == "higher":
        return raw
    return -raw


def combine_scores(robust_z: float, ewma_score: float) -> float:
    """Transparent combination: keep the stronger directional component."""
    return max(robust_z, ewma_score)


def evaluate_with_stats(
    *,
    baseline_median: float,
    baseline_dispersion: float,
    observed: float,
    direction: str,
    threshold: float,
    ewma_prev: float | None,
    alpha: float,
) -> DetectionResult:
    """Score an observation against frozen or computed baseline statistics."""
    mid = float(baseline_median)
    sigma = float(baseline_dispersion)
    if sigma <= 0:
        sigma = 1e-9

    z_raw = (observed - mid) / sigma
    z_dir = direction_score(z_raw, direction)

    prev = mid if ewma_prev is None else ewma_prev
    ewma_value = alpha * observed + (1.0 - alpha) * prev
    delta_raw = (observed - prev) / sigma
    delta_dir = direction_score(delta_raw, direction)

    combined = combine_scores(z_dir, delta_dir)
    is_candidate = z_dir > 0 and combined >= threshold

    return DetectionResult(
        observed=observed,
        baseline_median=mid,
        baseline_dispersion=sigma,
        robust_z=z_dir,
        ewma_score=delta_dir,
        combined_score=combined,
        direction=direction,
        is_candidate=is_candidate,
        ewma_value=ewma_value,
    )


def evaluate(
    baseline: Sequence[float],
    observed: float,
    *,
    direction: str,
    epsilon: float,
    threshold: float,
    ewma_prev: float | None,
    alpha: float,
    weight_z: float = 0.7,  # retained for call-site compat; unused in v2
    weight_ewma: float = 0.3,
) -> DetectionResult:
    """Compute baseline stats from samples, then score the observation."""
    del weight_z, weight_ewma
    mid, sigma = dispersion(baseline, epsilon)
    return evaluate_with_stats(
        baseline_median=mid,
        baseline_dispersion=sigma,
        observed=observed,
        direction=direction,
        threshold=threshold,
        ewma_prev=ewma_prev,
        alpha=alpha,
    )
