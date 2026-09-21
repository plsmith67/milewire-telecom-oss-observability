"""Webhook ingestion tests."""

from __future__ import annotations

from tests.conftest import alarm, webhook


def test_firing_webhook_opens_incident(client):
    response = client.post(
        "/webhooks/alertmanager",
        json=webhook(alarm("fp-outage")),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["processed"] == 1
    incident_id = body["incidents"][0]

    detail = client.get(f"/api/v1/incidents/{incident_id}").json()
    assert detail["site"] == "plte-site-103"
    assert detail["sector"] == "sector-gamma"
    assert detail["severity"] == "critical"
    assert detail["lifecycle_state"] == "active"
    assert detail["probable_domain"] == "RAN"
    assert detail["firing_alarm_count"] == 1
    assert "lte_availability_pct" in detail["affected_kpis"]


def test_duplicate_webhook_does_not_duplicate_alarm(client):
    payload = webhook(alarm("fp-dup"))
    first = client.post("/webhooks/alertmanager", json=payload)
    second = client.post("/webhooks/alertmanager", json=payload)
    assert first.status_code == 200
    assert second.status_code == 200
    incident_id = first.json()["incidents"][0]
    assert second.json()["incidents"] == [incident_id]

    alarms = client.get(f"/api/v1/incidents/{incident_id}/alarms").json()
    assert len(alarms) == 1
    assert alarms[0]["fingerprint"] == "fp-dup"

    listed = client.get("/api/v1/incidents").json()
    assert len(listed) == 1
