"""FastAPI anomaly detection service."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Response

from app.config import load_settings
from app.engine import AnomalyEngine
from app.metrics import MetricsRegistry, anomaly_to_dict
from app.models import EvaluateResponse, HealthResponse
from app.prom_client import PrometheusClient
from app.publisher import IncidentPublisher
from app.store import AnomalyStore

logger = logging.getLogger(__name__)


def create_app(
    db_path: str | None = None,
    *,
    enable_polling: bool = True,
    settings=None,
) -> FastAPI:
    cfg = settings or load_settings()
    if db_path:
        cfg.db_path = db_path

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = AnomalyStore(cfg.db_path)
        prom = PrometheusClient(cfg.prometheus_url)
        publisher = IncidentPublisher(cfg.incident_webhook_url)
        metrics = MetricsRegistry()
        engine = AnomalyEngine(cfg, store, prom, publisher, metrics)
        app.state.settings = cfg
        app.state.store = store
        app.state.prom = prom
        app.state.engine = engine
        app.state.metrics = metrics
        stop = asyncio.Event()
        task = None

        async def _poll():
            while not stop.is_set():
                try:
                    await asyncio.to_thread(engine.run_once)
                except Exception:  # noqa: BLE001
                    logger.exception("Anomaly evaluation failed")
                try:
                    await asyncio.wait_for(stop.wait(), timeout=cfg.poll_interval_sec)
                except asyncio.TimeoutError:
                    continue

        if enable_polling:
            task = asyncio.create_task(_poll())
        yield
        stop.set()
        if task is not None:
            await task
        store.close()

    app = FastAPI(
        title="Telecom OSS Anomaly Service",
        description=(
            "Detects statistically meaningful Private LTE KPI deviations "
            "relative to per-site/sector baselines using MAD, robust z-score, and EWMA."
        ),
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
        if not app.state.prom.healthy():
            raise HTTPException(status_code=503, detail="Prometheus not reachable")
        return HealthResponse(status="ready")

    @app.get("/metrics")
    def metrics() -> Response:
        app.state.metrics.refresh(app.state.store)
        payload, content_type = app.state.metrics.expose()
        return Response(content=payload, media_type=content_type)

    @app.get("/api/v1/anomalies")
    def list_anomalies(
        state: str | None = Query(default=None),
        site: str | None = Query(default=None),
        kpi: str | None = Query(default=None),
    ):
        rows = app.state.store.list(state=state, site=site, kpi=kpi)
        return [anomaly_to_dict(row) for row in rows]

    @app.get("/api/v1/anomalies/{anomaly_id}")
    def get_anomaly(anomaly_id: str):
        row = app.state.store.get(anomaly_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Anomaly not found")
        return anomaly_to_dict(row, include_window=True)

    @app.post("/api/v1/evaluate", response_model=EvaluateResponse)
    def evaluate_now() -> EvaluateResponse:
        result = app.state.engine.run_once()
        return EvaluateResponse(**result)

    return app


app = create_app()
