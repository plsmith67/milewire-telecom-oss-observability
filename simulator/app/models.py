"""Pydantic models and enums for the KPI simulator API."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class OperatingState(str, Enum):
    NORMAL = "normal"
    DEGRADED = "degraded"
    CRITICAL = "critical"


class FailureType(str, Enum):
    RF_INTERFERENCE = "rf_interference"
    BACKHAUL_DEGRADATION = "backhaul_degradation"
    CELL_OUTAGE = "cell_outage"
    CAPACITY_CONGESTION = "capacity_congestion"


class FailureRequest(BaseModel):
    site: str = Field(..., description="Target site ID, e.g. plte-site-101")
    sector: Optional[str] = Field(
        default=None,
        description=(
            "Optional sector ID (sector-alpha, sector-beta, sector-gamma). "
            "If omitted, applies to all sectors at the site."
        ),
    )


class Incident(BaseModel):
    id: str
    failure_type: FailureType
    site: str
    sector: Optional[str] = None
    created_at: datetime
    operating_state: OperatingState


class SectorKPIs(BaseModel):
    site: str
    sector: str
    sector_name: str
    state: OperatingState
    rsrp_dbm: float
    rsrq_db: float
    sinr_db: float
    dl_throughput_mbps: float
    ul_throughput_mbps: float
    packet_loss_pct: float
    latency_ms: float
    availability_pct: float
    active_users: float
    handover_success_pct: float
    active_failures: list[FailureType] = Field(default_factory=list)


class TopologyResponse(BaseModel):
    sites: list[dict[str, object]]


class HealthResponse(BaseModel):
    status: str


class MessageResponse(BaseModel):
    message: str
    cleared: int | None = None
