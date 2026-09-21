"""Acknowledgement and clearing lifecycle tests."""

from __future__ import annotations

from tests.conftest import alarm, webhook


def _open(client) -> str:
    response = client.post(
        "/webhooks/alertmanager",
        json=webhook(
            alarm("fp-1", alertname="PLTEHighLatency", severity="major", kpi="lte_latency_ms", domain="Transport"),
            alarm("fp-2", alertname="PLTEHighPacketLoss", severity="warning", kpi="lte_packet_loss_pct", domain="Transport"),
        ),
    )
    return response.json()["incidents"][0]


def test_acknowledge_active_incident(client):
    incident_id = _open(client)
    response = client.post(
        f"/api/v1/incidents/{incident_id}/acknowledge",
        json={"acknowledged_by": "noc-operator"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["lifecycle_state"] == "acknowledged"
    assert body["acknowledged_by"] == "noc-operator"
    assert body["acknowledged_at"]

    history = client.get(f"/api/v1/incidents/{incident_id}/history").json()
    assert any(event["event_type"] == "acknowledged" for event in history)
    listed = client.get("/api/v1/incidents", params={"state": "acknowledged"}).json()
    assert len(listed) == 1


def test_clear_when_all_alarms_resolve(client):
    incident_id = _open(client)
    client.post(f"/api/v1/incidents/{incident_id}/acknowledge", json={})
    resolved = client.post(
        "/webhooks/alertmanager",
        json=webhook(
            alarm("fp-1", alertname="PLTEHighLatency", severity="major", status="resolved", domain="Transport"),
            alarm("fp-2", alertname="PLTEHighPacketLoss", severity="warning", status="resolved", domain="Transport"),
        ),
    )
    assert resolved.status_code == 200
    detail = client.get(f"/api/v1/incidents/{incident_id}").json()
    assert detail["lifecycle_state"] == "cleared"
    assert detail["cleared_at"]
    assert detail["firing_alarm_count"] == 0
    assert detail["probable_domain"] == "Transport"
    history = client.get(f"/api/v1/incidents/{incident_id}/history").json()
    assert any(event["event_type"] == "cleared" for event in history)


def test_acknowledge_cleared_incident_is_rejected(client):
    incident_id = _open(client)
    client.post(
        "/webhooks/alertmanager",
        json=webhook(
            alarm("fp-1", alertname="PLTEHighLatency", severity="major", status="resolved", domain="Transport"),
            alarm("fp-2", alertname="PLTEHighPacketLoss", severity="warning", status="resolved", domain="Transport"),
        ),
    )
    response = client.post(f"/api/v1/incidents/{incident_id}/acknowledge", json={})
    assert response.status_code == 409
