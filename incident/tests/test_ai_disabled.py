"""AI disabled behavior."""

from tests.test_anomaly_correlation import _anomaly


def test_analyze_unavailable_without_key(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    opened = client.post("/webhooks/anomalies", json={"anomalies": [_anomaly()]})
    incident_id = opened.json()["incidents"][0]
    response = client.post(f"/api/v1/incidents/{incident_id}/analyze")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unavailable"
    assert "no provider configured" in body["message"]
    assert "analysis" not in body or body.get("analysis") is None
    history = client.get(f"/api/v1/incidents/{incident_id}/analyses").json()
    assert history[0]["status"] == "unavailable"
