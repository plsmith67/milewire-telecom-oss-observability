"""Pydantic schemas for the incident API."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class AckRequest(BaseModel):
    acknowledged_by: str = "operator"


class AlarmOut(BaseModel):
    id: str
    incident_id: str
    fingerprint: str
    alertname: str
    severity: str
    status: str
    affected_kpi: Optional[str] = None
    starts_at: Optional[str] = None
    ends_at: Optional[str] = None
    last_seen_at: str
    labels: dict[str, Any] = Field(default_factory=dict)
    annotations: dict[str, Any] = Field(default_factory=dict)


class EventOut(BaseModel):
    id: str
    incident_id: str
    event_type: str
    timestamp: str
    detail: dict[str, Any] = Field(default_factory=dict)


class IncidentOut(BaseModel):
    id: str
    site: str
    sector: str
    severity: str
    lifecycle_state: str
    probable_domain: str
    first_detected: str
    last_updated: str
    cleared_at: Optional[str] = None
    acknowledged_at: Optional[str] = None
    acknowledged_by: Optional[str] = None
    contributing_alarm_count: int = 0
    firing_alarm_count: int = 0
    affected_kpis: list[str] = Field(default_factory=list)


class IncidentDetail(IncidentOut):
    alarms: list[AlarmOut] = Field(default_factory=list)


class WebhookResult(BaseModel):
    processed: int
    incidents: list[str]
    errors: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str
