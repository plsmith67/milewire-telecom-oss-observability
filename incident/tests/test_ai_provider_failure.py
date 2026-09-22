"""AI provider failure does not lose incident data."""

from tests.test_anomaly_correlation import _anomaly


def test_provider_failure_keeps_incident(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")

    def boom(_evidence, client_factory=None):
        raise RuntimeError("provider down")

    monkeypatch.setattr("app.ai.analyze_with_openai", boom)
    opened = client.post("/webhooks/anomalies", json={"anomalies": [_anomaly()]})
    incident_id = opened.json()["incidents"][0]
    before = client.get(f"/api/v1/incidents/{incident_id}").json()
    response = client.post(f"/api/v1/incidents/{incident_id}/analyze")
    assert response.status_code == 502
    after = client.get(f"/api/v1/incidents/{incident_id}").json()
    assert after["id"] == before["id"]
    assert after["lifecycle_state"] == before["lifecycle_state"]
    assert after["contributing_anomaly_count"] == 1
    history = client.get(f"/api/v1/incidents/{incident_id}/analyses").json()
    assert history[0]["status"] == "failed"
