"""Invalid Alertmanager payload handling."""

from __future__ import annotations

from tests.conftest import alarm, webhook


def test_missing_alerts_array_is_rejected(client):
    response = client.post("/webhooks/alertmanager", json={"status": "firing"})
    assert response.status_code == 400
    assert client.get("/api/v1/incidents").json() == []


def test_invalid_alert_returns_400_but_keeps_valid_sibling(client):
    bad = alarm("fp-bad")
    bad["labels"].pop("site")
    response = client.post(
        "/webhooks/alertmanager",
        json=webhook(bad, alarm("fp-good", sector="sector-alpha")),
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["processed"] == 1
    assert detail["errors"]
    incidents = client.get("/api/v1/incidents").json()
    assert len(incidents) == 1
    assert incidents[0]["sector"] == "sector-alpha"


def test_invalid_severity_is_rejected(client):
    bad = alarm("fp-sev")
    bad["labels"]["severity"] = "panic"
    response = client.post("/webhooks/alertmanager", json=webhook(bad))
    assert response.status_code == 400
    assert client.get("/api/v1/incidents").json() == []


def test_health_ready_and_metrics(client):
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/ready").json()["status"] == "ready"
    client.post("/webhooks/alertmanager", json=webhook(alarm("fp-metrics")))
    body = client.get("/metrics").text
    assert "oss_incident_info" in body
    assert 'site="plte-site-103"' in body
    assert 'sector="sector-gamma"' in body
