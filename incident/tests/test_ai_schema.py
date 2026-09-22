"""AI schema, structured-output, and analyze-path tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.ai import (
    AIAnalysisSchema,
    AIIncompleteError,
    AIProviderError,
    AIRefusalError,
    AISchemaValidationError,
    EvidenceReference,
    analysis_json_schema,
    analyze_with_openai,
    openai_response_format,
    validate_analysis,
)
from tests.conftest import alarm, webhook
from tests.test_anomaly_correlation import _anomaly


def _valid_payload(**overrides):
    payload = {
        "executive_summary": "SINR drifted below baseline while RF interference is active.",
        "observed_evidence": [
            {
                "id": "anom-1",
                "evidence_type": "statistical_anomaly",
                "summary": "lte_sinr_db anomaly score 4.2 vs baseline median 20.0",
            },
            {
                "id": "alarm-1",
                "evidence_type": "threshold_alarm",
                "summary": "PLTELowSINR firing at major severity",
            },
        ],
        "probable_causes": ["RF interference onset affecting SINR"],
        "confidence": 0.62,
        "recommended_operator_checks": ["Review neighbor interference and RSRP/RSRQ"],
        "ruled_out_or_unsupported_causes": ["Cell outage (availability still high)"],
        "limitations": ["No neighbor cell telemetry in evidence pack"],
        "evidence_references": ["anom-1", "alarm-1"],
    }
    payload.update(overrides)
    return payload


def test_valid_strict_structured_output():
    model = validate_analysis(_valid_payload())
    assert isinstance(model.observed_evidence, list)
    assert all(isinstance(item, EvidenceReference) for item in model.observed_evidence)
    assert model.confidence == 0.62
    dumped = model.model_dump()
    for field in (
        "executive_summary",
        "observed_evidence",
        "probable_causes",
        "confidence",
        "recommended_operator_checks",
        "ruled_out_or_unsupported_causes",
        "limitations",
        "evidence_references",
    ):
        assert field in dumped


def test_observed_evidence_is_list():
    model = validate_analysis(_valid_payload())
    assert isinstance(model.observed_evidence, list)
    schema = analysis_json_schema()
    assert schema["properties"]["observed_evidence"]["type"] == "array"
    items = schema["properties"]["observed_evidence"]["items"]
    assert items.get("type") == "object" or items.get("$ref") == "#/$defs/EvidenceReference"
    assert "EvidenceReference" in (schema.get("$defs") or {})
    assert schema["$defs"]["EvidenceReference"]["type"] == "object"
    assert schema["$defs"]["EvidenceReference"]["additionalProperties"] is False


def test_dict_shaped_observed_evidence_rejected_locally():
    with pytest.raises(AISchemaValidationError, match="must be a list"):
        validate_analysis(
            _valid_payload(
                observed_evidence={
                    "threshold_alarms": [{"id": "a"}],
                    "statistical_anomalies": [{"id": "b"}],
                }
            )
        )


def test_extra_properties_rejected():
    with pytest.raises(AISchemaValidationError):
        validate_analysis(_valid_payload(unexpected_field="nope"))
    with pytest.raises(ValidationError):
        EvidenceReference.model_validate(
            {
                "id": "x",
                "evidence_type": "threshold_alarm",
                "summary": "ok",
                "extra": True,
            }
        )


def test_openai_response_format_is_strict_json_schema():
    fmt = openai_response_format()
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True
    schema = fmt["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"].keys())
    for name, defn in (schema.get("$defs") or {}).items():
        assert defn.get("additionalProperties") is False
        assert set(defn.get("required") or []) == set((defn.get("properties") or {}).keys())


def _fake_completion(*, content=None, refusal=None, finish_reason="stop", choices=None):
    if choices is not None:
        return SimpleNamespace(choices=choices)
    message = SimpleNamespace(content=content, refusal=refusal)
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice])


class _FakeCompletions:
    def __init__(self, response=None, error: Exception | None = None):
        self._response = response
        self._error = error
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        if self._error is not None:
            raise self._error
        return self._response


class _FakeChat:
    def __init__(self, completions: _FakeCompletions):
        self.completions = completions


class _FakeClient:
    def __init__(self, completions: _FakeCompletions):
        self.chat = _FakeChat(completions)


def test_analyze_valid_structured_output(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    payload = _valid_payload()
    completions = _FakeCompletions(
        response=_fake_completion(content=__import__("json").dumps(payload))
    )
    result = analyze_with_openai({"incident": {"id": "i"}}, client_factory=lambda: _FakeClient(completions))
    assert result["executive_summary"]
    assert isinstance(result["observed_evidence"], list)
    assert completions.last_kwargs["response_format"]["json_schema"]["strict"] is True


def test_analyze_refusal(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    completions = _FakeCompletions(
        response=_fake_completion(content=None, refusal="Cannot analyze this content")
    )
    with pytest.raises(AIRefusalError, match="refused"):
        analyze_with_openai({}, client_factory=lambda: _FakeClient(completions))


def test_analyze_incomplete(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    completions = _FakeCompletions(
        response=_fake_completion(content='{"executive_summary":', finish_reason="length")
    )
    with pytest.raises(AIIncompleteError, match="Incomplete"):
        analyze_with_openai({}, client_factory=lambda: _FakeClient(completions))


def test_analyze_provider_error(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    completions = _FakeCompletions(error=RuntimeError("upstream 503"))
    with pytest.raises(AIProviderError, match="provider failure"):
        analyze_with_openai({}, client_factory=lambda: _FakeClient(completions))


def test_analyze_local_schema_failure_for_dict_evidence(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    bad = _valid_payload(
        observed_evidence={"threshold_alarms": [], "statistical_anomalies": []}
    )
    completions = _FakeCompletions(
        response=_fake_completion(content=__import__("json").dumps(bad))
    )
    with pytest.raises(AISchemaValidationError):
        analyze_with_openai({}, client_factory=lambda: _FakeClient(completions))


def test_http_completed_analysis_fields_and_grounding(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    monkeypatch.delenv("AI_PROMPT_VERSION", raising=False)

    opened = client.post("/webhooks/anomalies", json={"anomalies": [_anomaly(id="anom-sinr")]})
    incident_id = opened.json()["incidents"][0]
    client.post(
        "/webhooks/alertmanager",
        json=webhook(
            alarm(
                "fp-sinr",
                alertname="PLTELowSINR",
                severity="major",
                kpi="lte_sinr_db",
                domain="RF",
            )
        ),
    )
    detail = client.get(f"/api/v1/incidents/{incident_id}").json()
    alarms = client.get(f"/api/v1/incidents/{incident_id}/alarms").json()
    anoms = client.get(f"/api/v1/incidents/{incident_id}/anomalies").json()
    alarm_id = alarms[0]["id"]
    anom_id = anoms[0]["anomaly_id"]
    history_before = client.get(f"/api/v1/incidents/{incident_id}/history").json()

    def fake_analyze(evidence_pack, client_factory=None):
        allowed = {a["id"] for a in evidence_pack["threshold_alarms"]} | {
            a["id"] for a in evidence_pack["statistical_anomalies"]
        }
        assert alarm_id in allowed and anom_id in allowed
        return validate_analysis(
            _valid_payload(
                observed_evidence=[
                    {
                        "id": anom_id,
                        "evidence_type": "statistical_anomaly",
                        "summary": "SINR statistical anomaly active",
                    },
                    {
                        "id": alarm_id,
                        "evidence_type": "threshold_alarm",
                        "summary": "PLTELowSINR threshold alert firing",
                    },
                ],
                probable_causes=[f"RF degradation supported by {anom_id} and {alarm_id}"],
                recommended_operator_checks=[
                    f"Inspect RF path for evidence {alarm_id}/{anom_id}"
                ],
                evidence_references=[anom_id, alarm_id],
            )
        ).model_dump()

    monkeypatch.setattr("app.ai.analyze_with_openai", fake_analyze)

    response = client.post(f"/api/v1/incidents/{incident_id}/analyze")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["provider"] == "openai"
    assert body["prompt_version"] == "triage-v2"
    analysis = body["analysis"]
    AIAnalysisSchema.model_validate(analysis)
    assert isinstance(analysis["observed_evidence"], list)

    known = {a["id"] for a in alarms} | {a["anomaly_id"] for a in anoms}
    for ref in analysis["evidence_references"]:
        assert ref in known
    for item in analysis["observed_evidence"]:
        assert item["id"] in known

    after = client.get(f"/api/v1/incidents/{incident_id}").json()
    assert after["lifecycle_state"] == detail["lifecycle_state"]
    assert after["severity"] == detail["severity"]
    assert [a["id"] for a in client.get(f"/api/v1/incidents/{incident_id}/alarms").json()] == [
        a["id"] for a in alarms
    ]
    assert [
        a["anomaly_id"] for a in client.get(f"/api/v1/incidents/{incident_id}/anomalies").json()
    ] == [a["anomaly_id"] for a in anoms]

    history_after = client.get(f"/api/v1/incidents/{incident_id}/history").json()
    # Analyze must not mutate correlation history events; analyses are separate.
    assert history_after == history_before
    analyses = client.get(f"/api/v1/incidents/{incident_id}/analyses").json()
    assert analyses[0]["status"] == "completed"
    assert analyses[0]["analysis"] is not None


def test_http_refusal_stores_refused_status(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")

    def boom(_pack, client_factory=None):
        raise AIRefusalError("Model refused to analyze: policy")

    monkeypatch.setattr("app.ai.analyze_with_openai", boom)
    opened = client.post("/webhooks/anomalies", json={"anomalies": [_anomaly()]})
    incident_id = opened.json()["incidents"][0]
    response = client.post(f"/api/v1/incidents/{incident_id}/analyze")
    assert response.status_code == 502
    assert response.json()["detail"]["status"] == "refused"
    stored = client.get(f"/api/v1/incidents/{incident_id}/analyses").json()[0]
    assert stored["status"] == "refused"
    assert stored["analysis"] is None
    assert stored["evidence_references"]


def test_http_incomplete_stores_incomplete_status(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")

    def boom(_pack, client_factory=None):
        raise AIIncompleteError("Incomplete AI response (finish_reason=length)")

    monkeypatch.setattr("app.ai.analyze_with_openai", boom)
    opened = client.post("/webhooks/anomalies", json={"anomalies": [_anomaly()]})
    incident_id = opened.json()["incidents"][0]
    response = client.post(f"/api/v1/incidents/{incident_id}/analyze")
    assert response.status_code == 502
    assert response.json()["detail"]["status"] == "incomplete"
    stored = client.get(f"/api/v1/incidents/{incident_id}/analyses").json()[0]
    assert stored["status"] == "incomplete"
    assert stored["analysis"] is None


def test_provider_failure_keeps_incident(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")

    def boom(_evidence, client_factory=None):
        raise AIProviderError("provider down")

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
    assert history[0]["analysis"] is None
