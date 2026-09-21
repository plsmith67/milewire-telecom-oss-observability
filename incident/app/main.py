"""FastAPI incident service."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Response

from app.correlator import Correlator
from app.metrics import MetricsRegistry, alarm_to_dict, incident_to_dict
from app.models import AckRequest, HealthResponse, WebhookResult
from app.store import IncidentStore
from app.webhook import PayloadError, parse_webhook


def create_app(db_path: str | None = None) -> FastAPI:
    path = db_path or os.environ.get("INCIDENT_DB_PATH", "/data/incidents.db")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = IncidentStore(path)
        app.state.store = store
        app.state.correlator = Correlator(store)
        app.state.metrics = MetricsRegistry()
        app.state.metrics.refresh(store)
        yield
        store.close()

    app = FastAPI(
        title="Telecom OSS Incident Service",
        description="Correlates Private LTE Prometheus alerts into operational incidents.",
        version="1.0.0",
        lifespan=lifespan,
    )

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.get("/ready", response_model=HealthResponse)
    def ready() -> HealthResponse:
        if not hasattr(app.state, "store"):
            raise HTTPException(status_code=503, detail="Store not ready")
        return HealthResponse(status="ready")

    @app.get("/metrics")
    def metrics() -> Response:
        app.state.metrics.refresh(app.state.store)
        payload, content_type = app.state.metrics.expose()
        return Response(content=payload, media_type=content_type)

    @app.post("/webhooks/alertmanager", response_model=WebhookResult)
    def ingest_webhook(body: dict) -> WebhookResult:
        try:
            alarms, errors = parse_webhook(body)
        except PayloadError as exc:
            app.state.metrics.webhook_requests.labels(result="invalid").inc()
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        incident_ids = app.state.correlator.ingest(alarms) if alarms else []
        app.state.metrics.refresh(app.state.store)
        result = WebhookResult(
            processed=len(alarms),
            incidents=[item for item in incident_ids if item],
            errors=errors,
        )
        if errors:
            app.state.metrics.webhook_requests.labels(result="partial").inc()
            raise HTTPException(status_code=400, detail=result.model_dump())
        app.state.metrics.webhook_requests.labels(result="ok").inc()
        return result

    @app.get("/api/v1/incidents")
    def list_incidents(
        state: str | None = Query(default=None),
        site: str | None = Query(default=None),
        severity: str | None = Query(default=None),
    ):
        rows = app.state.store.list_incidents(state=state, site=site, severity=severity)
        return [incident_to_dict(app.state.store, row) for row in rows]

    @app.get("/api/v1/incidents/{incident_id}")
    def get_incident(incident_id: str):
        row = app.state.store.get_incident(incident_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Incident not found")
        return incident_to_dict(app.state.store, row, include_alarms=True)

    @app.get("/api/v1/incidents/{incident_id}/alarms")
    def get_alarms(incident_id: str):
        if app.state.store.get_incident(incident_id) is None:
            raise HTTPException(status_code=404, detail="Incident not found")
        return [alarm_to_dict(row) for row in app.state.store.list_alarms(incident_id)]

    @app.get("/api/v1/incidents/{incident_id}/history")
    def get_history(incident_id: str):
        if app.state.store.get_incident(incident_id) is None:
            raise HTTPException(status_code=404, detail="Incident not found")
        import json

        events = []
        for row in app.state.store.list_events(incident_id):
            events.append(
                {
                    "id": row["id"],
                    "incident_id": row["incident_id"],
                    "event_type": row["event_type"],
                    "timestamp": row["timestamp"],
                    "detail": json.loads(row["detail_json"]),
                }
            )
        return events

    @app.post("/api/v1/incidents/{incident_id}/acknowledge")
    def acknowledge(incident_id: str, body: AckRequest | None = None):
        actor = body.acknowledged_by if body else "operator"
        outcome = app.state.correlator.acknowledge(incident_id, actor)
        if outcome == "missing":
            raise HTTPException(status_code=404, detail="Incident not found")
        if outcome == "cleared":
            raise HTTPException(status_code=409, detail="Cleared incidents cannot be acknowledged")
        app.state.metrics.refresh(app.state.store)
        row = app.state.store.get_incident(incident_id)
        return incident_to_dict(app.state.store, row)

    return app


app = create_app()
