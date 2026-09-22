"""FastAPI incident service."""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Response

from app import ai as ai_mod
from app.correlator import Correlator
from app.metrics import (
    MetricsRegistry,
    alarm_to_dict,
    anomaly_contrib_to_dict,
    incident_to_dict,
)
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
        description=(
            "Correlates Private LTE Prometheus alerts and statistical anomalies "
            "into operational incidents, with optional evidence-grounded AI triage."
        ),
        version="2.0.0",
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

    @app.post("/webhooks/anomalies", response_model=WebhookResult)
    def ingest_anomalies(body: dict) -> WebhookResult:
        anomalies = body.get("anomalies")
        if not isinstance(anomalies, list):
            app.state.metrics.anomaly_webhook_requests.labels(result="invalid").inc()
            raise HTTPException(status_code=400, detail="anomalies must be a list")
        valid = []
        errors = []
        for idx, item in enumerate(anomalies):
            if not isinstance(item, dict):
                errors.append(f"anomalies[{idx}] must be an object")
                continue
            missing = [key for key in ("id", "site", "sector", "kpi") if not item.get(key)]
            if missing:
                errors.append(f"anomalies[{idx}] missing {', '.join(missing)}")
                continue
            valid.append(item)
        incident_ids = app.state.correlator.ingest_anomalies(valid) if valid else []
        app.state.metrics.refresh(app.state.store)
        result = WebhookResult(
            processed=len(valid),
            incidents=[item for item in incident_ids if item],
            errors=errors,
        )
        if errors:
            app.state.metrics.anomaly_webhook_requests.labels(result="partial").inc()
            raise HTTPException(status_code=400, detail=result.model_dump())
        app.state.metrics.anomaly_webhook_requests.labels(result="ok").inc()
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

    @app.get("/api/v1/incidents/{incident_id}/anomalies")
    def get_incident_anomalies(incident_id: str):
        if app.state.store.get_incident(incident_id) is None:
            raise HTTPException(status_code=404, detail="Incident not found")
        return [anomaly_contrib_to_dict(row) for row in app.state.store.list_anomalies(incident_id)]

    @app.get("/api/v1/incidents/{incident_id}/evidence")
    def get_evidence(incident_id: str):
        if app.state.store.get_incident(incident_id) is None:
            raise HTTPException(status_code=404, detail="Incident not found")
        alarms = [alarm_to_dict(row) for row in app.state.store.list_alarms(incident_id)]
        anomalies = [anomaly_contrib_to_dict(row) for row in app.state.store.list_anomalies(incident_id)]
        return {"threshold_alarms": alarms, "statistical_anomalies": anomalies}

    @app.get("/api/v1/incidents/{incident_id}/history")
    def get_history(incident_id: str):
        if app.state.store.get_incident(incident_id) is None:
            raise HTTPException(status_code=404, detail="Incident not found")
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

    @app.get("/api/v1/anomalies")
    def list_anomalies():
        return [anomaly_contrib_to_dict(row) for row in app.state.store.list_anomalies()]

    @app.get("/api/v1/anomalies/{anomaly_id}")
    def get_anomaly(anomaly_id: str):
        row = app.state.store.get_anomaly(anomaly_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Anomaly contribution not found")
        return anomaly_contrib_to_dict(row)

    @app.post("/api/v1/incidents/{incident_id}/analyze")
    def analyze_incident(incident_id: str):
        incident = app.state.store.get_incident(incident_id)
        if incident is None:
            raise HTTPException(status_code=404, detail="Incident not found")

        alarms = [alarm_to_dict(row) for row in app.state.store.list_alarms(incident_id)]
        anomalies = [anomaly_contrib_to_dict(row) for row in app.state.store.list_anomalies(incident_id)]
        evidence_pack = ai_mod.build_evidence_pack(
            incident_to_dict(app.state.store, incident),
            alarms,
            anomalies,
        )
        evidence_refs = [item["id"] for item in alarms] + [item["anomaly_id"] for item in anomalies]

        if not ai_mod.provider_configured():
            analysis_id = app.state.store.insert_analysis(
                incident_id=incident_id,
                provider="none",
                model="none",
                prompt_version=ai_mod.prompt_version(),
                status="unavailable",
                response=None,
                evidence_refs=evidence_refs,
                error="AI analysis is unavailable; no provider configured.",
            )
            app.state.metrics.refresh(app.state.store)
            return {
                "id": analysis_id,
                "status": "unavailable",
                "message": "AI analysis is unavailable; no provider configured.",
                "provider": "none",
                "model": "none",
                "prompt_version": ai_mod.prompt_version(),
                "evidence_references": evidence_refs,
            }

        try:
            analysis = ai_mod.analyze_with_openai(evidence_pack)
            analysis_id = app.state.store.insert_analysis(
                incident_id=incident_id,
                provider="openai",
                model=ai_mod.model_name(),
                prompt_version=ai_mod.prompt_version(),
                status="completed",
                response=analysis,
                evidence_refs=evidence_refs,
            )
            app.state.metrics.refresh(app.state.store)
            return {
                "id": analysis_id,
                "status": "completed",
                "provider": "openai",
                "model": ai_mod.model_name(),
                "prompt_version": ai_mod.prompt_version(),
                "analysis": analysis,
                "evidence_references": evidence_refs,
            }
        except ai_mod.AIAnalysisError as exc:
            status = exc.status
            messages = {
                "refused": "AI model refused to analyze the incident",
                "incomplete": "AI provider returned an incomplete response",
                "failed": "AI analysis failed",
            }
            analysis_id = app.state.store.insert_analysis(
                incident_id=incident_id,
                provider="openai",
                model=ai_mod.model_name(),
                prompt_version=ai_mod.prompt_version(),
                status=status,
                response=None,
                evidence_refs=evidence_refs,
                error=exc.message,
            )
            app.state.metrics.refresh(app.state.store)
            raise HTTPException(
                status_code=502,
                detail={
                    "id": analysis_id,
                    "status": status,
                    "message": messages.get(status, "AI analysis failed"),
                    "error": exc.message,
                    "provider": "openai",
                    "model": ai_mod.model_name(),
                    "prompt_version": ai_mod.prompt_version(),
                    "evidence_references": evidence_refs,
                },
            ) from exc
        except Exception as exc:  # noqa: BLE001 - unexpected failures
            analysis_id = app.state.store.insert_analysis(
                incident_id=incident_id,
                provider="openai",
                model=ai_mod.model_name(),
                prompt_version=ai_mod.prompt_version(),
                status="failed",
                response=None,
                evidence_refs=evidence_refs,
                error=str(exc),
            )
            app.state.metrics.refresh(app.state.store)
            raise HTTPException(
                status_code=502,
                detail={
                    "id": analysis_id,
                    "status": "failed",
                    "message": "AI provider failure",
                    "error": str(exc),
                    "provider": "openai",
                    "model": ai_mod.model_name(),
                    "prompt_version": ai_mod.prompt_version(),
                    "evidence_references": evidence_refs,
                },
            ) from exc

    @app.get("/api/v1/incidents/{incident_id}/analyses")
    def list_analyses(incident_id: str):
        if app.state.store.get_incident(incident_id) is None:
            raise HTTPException(status_code=404, detail="Incident not found")
        rows = []
        for row in app.state.store.list_analyses(incident_id):
            rows.append(
                {
                    "id": row["id"],
                    "incident_id": row["incident_id"],
                    "provider": row["provider"],
                    "model": row["model"],
                    "prompt_version": row["prompt_version"],
                    "created_at": row["created_at"],
                    "status": row["status"],
                    "analysis": json.loads(row["response_json"]) if row["response_json"] else None,
                    "evidence_references": json.loads(row["evidence_ref_json"]),
                    "error": row["error"],
                }
            )
        return rows

    return app


app = create_app()
