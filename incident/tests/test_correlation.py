"""Correlation of simultaneous alarms for one site and sector."""

from __future__ import annotations

from tests.conftest import alarm, webhook


def test_same_sector_alarms_correlate_into_one_incident(client):
    response = client.post(
        "/webhooks/alertmanager",
        json=webhook(
            alarm("fp-sinr", alertname="PLTELowSINR", severity="major", kpi="lte_sinr_db", domain="RF"),
            alarm(
                "fp-outage",
                alertname="PLTECellOutage",
                severity="critical",
                kpi="lte_availability_pct",
                domain="RAN",
                starts_at="2026-09-21T00:01:00Z",
            ),
        ),
    )
    assert response.status_code == 200
    assert len(response.json()["incidents"]) == 1
    incident_id = response.json()["incidents"][0]
    detail = client.get(f"/api/v1/incidents/{incident_id}").json()
    assert detail["contributing_alarm_count"] == 2
    assert detail["severity"] == "critical"
    assert detail["probable_domain"] == "Mixed"
    names = {item["alertname"] for item in detail["alarms"]}
    assert names == {"PLTELowSINR", "PLTECellOutage"}

    client.post(
        "/webhooks/alertmanager",
        json=webhook(
            alarm("fp-sinr", alertname="PLTELowSINR", severity="major", status="resolved", domain="RF")
        ),
    )
    after = client.get(f"/api/v1/incidents/{incident_id}").json()
    assert after["lifecycle_state"] == "active"
    assert after["probable_domain"] == "Mixed"
    assert after["firing_alarm_count"] == 1


def test_different_sectors_stay_separate(client):
    response = client.post(
        "/webhooks/alertmanager",
        json=webhook(
            alarm("fp-a", sector="sector-alpha"),
            alarm("fp-b", sector="sector-beta"),
        ),
    )
    assert response.status_code == 200
    assert len(response.json()["incidents"]) == 2


def test_severity_escalates_when_critical_alarm_joins(client):
    opened = client.post(
        "/webhooks/alertmanager",
        json=webhook(
            alarm("fp-warn", alertname="PLTEPoorRSRP", severity="warning", kpi="lte_rsrp_dbm", domain="RF")
        ),
    )
    incident_id = opened.json()["incidents"][0]
    assert client.get(f"/api/v1/incidents/{incident_id}").json()["severity"] == "warning"

    client.post(
        "/webhooks/alertmanager",
        json=webhook(alarm("fp-crit", alertname="PLTECellOutage", severity="critical")),
    )
    detail = client.get(f"/api/v1/incidents/{incident_id}").json()
    assert detail["severity"] == "critical"
    assert detail["contributing_alarm_count"] == 2
    history = client.get(f"/api/v1/incidents/{incident_id}/history").json()
    assert any(event["event_type"] == "severity_changed" for event in history)
