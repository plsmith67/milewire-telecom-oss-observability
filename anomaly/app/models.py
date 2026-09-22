"""Pydantic schemas for the anomaly API."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str


class EvaluateResponse(BaseModel):
    evaluated: int
    published: int
    anomalies: list[str] = Field(default_factory=list)


class AnomalyOut(BaseModel):
    id: str
    fingerprint: str
    site: str
    sector: str
    kpi: str
    observed_value: float
    baseline_median: float
    baseline_dispersion: float
    robust_z: float
    ewma_score: float
    combined_score: float
    direction: str
    lifecycle_state: str
    first_detected: str
    last_observed: str
    cleared_at: Optional[str] = None
    detector_version: str
    incident_id: Optional[str] = None
    sample_window: list[dict[str, Any]] = Field(default_factory=list)
