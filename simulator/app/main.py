"""FastAPI application for Private LTE KPI simulation and failure injection."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import PlainTextResponse

from app.failures import IncidentStore
from app.metrics import MetricsRegistry
from app.models import (
    FailureRequest,
    FailureType,
    HealthResponse,
    Incident,
    MessageResponse,
    TopologyResponse,
)
from app.simulator import NetworkSimulator


class AppState:
    simulator: NetworkSimulator
    store: IncidentStore
    metrics: MetricsRegistry


state = AppState()


def _refresh_metrics() -> None:
    state.simulator.sync_from_incidents(state.store)
    state.metrics.update(state.simulator, state.store)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    state.simulator = NetworkSimulator(seed=42)
    state.store = IncidentStore()
    state.metrics = MetricsRegistry()
    _refresh_metrics()
    yield


app = FastAPI(
    title="Telecom OSS KPI Simulator",
    description=(
        "Private LTE KPI simulator exposing Prometheus metrics and "
        "failure-injection APIs for observability demos."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.get("/ready", response_model=HealthResponse)
def ready() -> HealthResponse:
    if not hasattr(state, "simulator") or state.simulator is None:
        raise HTTPException(status_code=503, detail="Simulator not ready")
    return HealthResponse(status="ready")


@app.get("/metrics")
def metrics() -> Response:
    # Refresh KPIs on each scrape so Prometheus sees live drift
    _refresh_metrics()
    payload, content_type = state.metrics.expose()
    return Response(content=payload, media_type=content_type)


@app.get("/api/v1/topology", response_model=TopologyResponse)
def topology() -> TopologyResponse:
    return TopologyResponse(**state.simulator.topology())


@app.get("/api/v1/sectors")
def list_sectors():
    _refresh_metrics()
    return [sector.model_dump() for sector in state.simulator.list_sectors()]


@app.get("/failures", response_model=list[Incident])
def list_failures() -> list[Incident]:
    return state.store.list()


def _inject(failure_type: FailureType, body: FailureRequest) -> Incident:
    try:
        incident = state.store.create(failure_type, body.site, body.sector)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _refresh_metrics()
    return incident


@app.post("/failures/rf-interference", response_model=Incident, status_code=201)
def rf_interference(body: FailureRequest) -> Incident:
    return _inject(FailureType.RF_INTERFERENCE, body)


@app.post("/failures/backhaul-degradation", response_model=Incident, status_code=201)
def backhaul_degradation(body: FailureRequest) -> Incident:
    return _inject(FailureType.BACKHAUL_DEGRADATION, body)


@app.post("/failures/cell-outage", response_model=Incident, status_code=201)
def cell_outage(body: FailureRequest) -> Incident:
    return _inject(FailureType.CELL_OUTAGE, body)


@app.post("/failures/capacity-congestion", response_model=Incident, status_code=201)
def capacity_congestion(body: FailureRequest) -> Incident:
    return _inject(FailureType.CAPACITY_CONGESTION, body)


@app.post("/failures/soft-kpi-drift", response_model=Incident, status_code=201)
def soft_kpi_drift(body: FailureRequest) -> Incident:
    return _inject(FailureType.SOFT_KPI_DRIFT, body)


@app.delete("/failures/{failure_id}", response_model=MessageResponse)
def delete_failure(failure_id: str) -> MessageResponse:
    if not state.store.delete(failure_id):
        raise HTTPException(status_code=404, detail="Failure not found")
    _refresh_metrics()
    return MessageResponse(message="failure cleared", cleared=1)


@app.delete("/failures", response_model=MessageResponse)
def clear_failures() -> MessageResponse:
    cleared = state.store.clear()
    _refresh_metrics()
    return MessageResponse(message="all failures cleared", cleared=cleared)


@app.get("/", response_class=PlainTextResponse)
def root() -> str:
    return (
        "Telecom OSS KPI Simulator\n"
        "Endpoints: /health /ready /metrics /api/v1/topology "
        "/api/v1/sectors /failures\n"
    )
