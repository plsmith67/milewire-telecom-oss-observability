"""Anomaly webhook correlation tests."""

from __future__ import annotations

from tests.conftest import alarm, webhook


def _anomaly(**overrides):
    payload = {
        "id": "anom-1",
        "site": "plte-site-103",
        "sector": "sector-gamma",
        "kpi": "lte_sinr_db",
        "combined_score": 4.2,
        "status": "active",
        "domain": "RF",
        "first_detected": "2026-09-21T12:00:00Z",
        "lifecycle_state": "active",
        "observed_value": 12.0,
        "baseline_median": 20.0,
        "sample_window": [],
    }
    payload.update(overrides)
    return payload


def test_anomaly_opens_incident(client):
    response = client.post("/webhooks/anomalies", json={"anomalies": [_anomaly()]})
    assert response.status_code == 200
    incident_id = response.json()["incidents"][0]
    detail = client.get(f"/api/v1/incidents/{incident_id}").json()
    assert detail["contributing_anomaly_count"] == 1
    assert detail["severity"] == "warning"
    assert detail["probable_domain"] == "RF"
    evidence = client.get(f"/api/v1/incidents/{incident_id}/evidence").json()
    assert evidence["statistical_anomalies"][0]["evidence_type"] == "statistical_anomaly"


def test_anomaly_and_alarm_same_incident(client):
    opened = client.post("/webhooks/anomalies", json={"anomalies": [_anomaly()]})
    incident_id = opened.json()["incidents"][0]
    client.post(
        "/webhooks/alertmanager",
        json=webhook(
            alarm(
                "fp-lat",
                alertname="PLTEHighLatency",
                severity="major",
                kpi="lte_latency_ms",
                domain="Transport",
            )
        ),
    )
    detail = client.get(f"/api/v1/incidents/{incident_id}").json()
    assert detail["contributing_anomaly_count"] == 1
    assert detail["contributing_alarm_count"] == 1
    assert detail["severity"] == "major"
    assert detail["probable_domain"] == "Mixed"


def test_clear_requires_alarms_and_anomalies_resolved(client):
    opened = client.post("/webhooks/anomalies", json={"anomalies": [_anomaly()]})
    incident_id = opened.json()["incidents"][0]
    client.post(
        "/webhooks/alertmanager",
        json=webhook(alarm("fp-1", alertname="PLTEHighLatency", severity="warning", domain="Transport")),
    )
    client.post(
        "/webhooks/alertmanager",
        json=webhook(
            alarm(
                "fp-1",
                alertname="PLTEHighLatency",
                severity="warning",
                status="resolved",
                domain="Transport",
            )
        ),
    )
    still = client.get(f"/api/v1/incidents/{incident_id}").json()
    assert still["lifecycle_state"] in {"active", "acknowledged"}
    client.post(
        "/webhooks/anomalies",
        json={"anomalies": [_anomaly(status="cleared", lifecycle_state="cleared")]},
    )
    cleared = client.get(f"/api/v1/incidents/{incident_id}").json()
    assert cleared["lifecycle_state"] == "cleared"


def test_duplicate_anomaly_idempotent(client):
    client.post("/webhooks/anomalies", json={"anomalies": [_anomaly()]})
    client.post("/webhooks/anomalies", json={"anomalies": [_anomaly(combined_score=4.5)]})
    listed = client.get("/api/v1/anomalies").json()
    assert len(listed) == 1
    assert listed[0]["score"] == 4.5
