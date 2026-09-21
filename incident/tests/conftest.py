"""Shared fixtures for incident service tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def client(tmp_path):
    app = create_app(str(tmp_path / "incidents.db"))
    with TestClient(app) as test_client:
        yield test_client


def alarm(
    fingerprint: str,
    *,
    alertname: str = "PLTECellOutage",
    severity: str = "critical",
    site: str = "plte-site-103",
    sector: str = "sector-gamma",
    status: str = "firing",
    kpi: str = "lte_availability_pct",
    domain: str = "RAN",
    starts_at: str = "2026-09-21T00:00:00Z",
) -> dict:
    return {
        "status": status,
        "labels": {
            "alertname": alertname,
            "severity": severity,
            "site": site,
            "sector": sector,
            "domain": domain,
        },
        "annotations": {"kpi": kpi, "summary": f"{alertname} on {site}/{sector}"},
        "startsAt": starts_at,
        "endsAt": None if status == "firing" else "2026-09-21T00:05:00Z",
        "fingerprint": fingerprint,
    }


def webhook(*alerts: dict) -> dict:
    return {"version": "4", "status": "firing", "alerts": list(alerts)}
