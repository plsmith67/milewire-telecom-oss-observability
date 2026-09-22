"""Optional evidence-grounded OpenAI incident analysis."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger(__name__)

PROMPT_VERSION = "triage-v2"

SYSTEM_PROMPT = """You are an assistant helping telecom operators triage Private LTE incidents.
Analyze ONLY the evidence JSON provided by the caller.
Never invent telemetry, alarms, anomalies, KPI values, or timestamps that are not present.
Distinguish observed facts, inferences, and recommended operator checks.
observed_evidence must be a JSON array of evidence-reference objects (never a bare object/dict).
Each observed_evidence item must cite an alarm or anomaly id from the evidence pack.
Cite evidence_references using those same alarm and anomaly IDs.
Do not recommend autonomous remediation; suggest human operator checks only.
Do not claim certain root cause, predictive maintenance, autonomous remediation, or production readiness.
Return JSON that matches the provided strict schema exactly.
"""


class EvidenceReference(BaseModel):
    """Reference to an alarm or anomaly supplied in the evidence pack."""

    model_config = ConfigDict(extra="forbid")

    id: str
    evidence_type: Literal["threshold_alarm", "statistical_anomaly"]
    summary: str


class AIAnalysisSchema(BaseModel):
    """Local validation schema; also the source of the OpenAI strict JSON Schema."""

    model_config = ConfigDict(extra="forbid")

    executive_summary: str
    observed_evidence: list[EvidenceReference]
    probable_causes: list[str]
    confidence: float
    recommended_operator_checks: list[str]
    ruled_out_or_unsupported_causes: list[str]
    limitations: list[str]
    evidence_references: list[str]


class AIAnalysisError(Exception):
    """Base class for AI triage failures."""

    status: str = "failed"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class AIRefusalError(AIAnalysisError):
    status = "refused"


class AIIncompleteError(AIAnalysisError):
    status = "incomplete"


class AIProviderError(AIAnalysisError):
    status = "failed"


class AISchemaValidationError(AIAnalysisError):
    status = "failed"


def provider_configured() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def model_name() -> str:
    return os.environ.get("OPENAI_MODEL", "gpt-4o-mini")


def prompt_version() -> str:
    return os.environ.get("AI_PROMPT_VERSION", PROMPT_VERSION)


def build_evidence_pack(incident: dict[str, Any], alarms: list[dict], anomalies: list[dict]) -> dict[str, Any]:
    return {
        "incident": {
            "id": incident["id"],
            "site": incident["site"],
            "sector": incident["sector"],
            "severity": incident["severity"],
            "lifecycle_state": incident["lifecycle_state"],
            "probable_domain": incident["probable_domain"],
            "first_detected": incident["first_detected"],
            "last_updated": incident["last_updated"],
        },
        "threshold_alarms": [
            {
                "id": item["id"],
                "evidence_type": "threshold_alarm",
                "alertname": item["alertname"],
                "severity": item["severity"],
                "status": item["status"],
                "affected_kpi": item.get("affected_kpi"),
                "starts_at": item.get("starts_at"),
                "ends_at": item.get("ends_at"),
            }
            for item in alarms
        ],
        "statistical_anomalies": [
            {
                "id": item["anomaly_id"],
                "evidence_type": "statistical_anomaly",
                "kpi": item["kpi"],
                "score": item["score"],
                "status": item["status"],
                "domain": item.get("domain"),
                "evidence": item.get("evidence") or {},
            }
            for item in anomalies
        ],
    }


def validate_analysis(payload: dict[str, Any]) -> AIAnalysisSchema:
    """Defense-in-depth local validation. Never coerces dict→list."""
    if isinstance(payload.get("observed_evidence"), dict):
        raise AISchemaValidationError(
            "observed_evidence must be a list of evidence-reference objects, not an object/dict"
        )
    try:
        return AIAnalysisSchema.model_validate(payload)
    except ValidationError as exc:
        raise AISchemaValidationError(f"Invalid AI response schema: {exc}") from exc


def _ensure_strict_object(node: Any) -> Any:
    """Force OpenAI strict-mode constraints on a JSON Schema tree."""
    if isinstance(node, list):
        return [_ensure_strict_object(item) for item in node]
    if not isinstance(node, dict):
        return node

    out = {key: _ensure_strict_object(value) for key, value in node.items()}
    if out.get("type") == "object" or "properties" in out:
        out["type"] = "object"
        out["additionalProperties"] = False
        props = out.get("properties") or {}
        out["properties"] = props
        # Strict mode requires every property key to appear in required.
        out["required"] = list(props.keys())
    if "items" in out:
        out["items"] = _ensure_strict_object(out["items"])
    if "$defs" in out:
        out["$defs"] = {
            name: _ensure_strict_object(defn) for name, defn in out["$defs"].items()
        }
    if "definitions" in out:
        out["definitions"] = {
            name: _ensure_strict_object(defn) for name, defn in out["definitions"].items()
        }
    if "anyOf" in out:
        out["anyOf"] = _ensure_strict_object(out["anyOf"])
    return out


def analysis_json_schema() -> dict[str, Any]:
    """Derive the provider JSON Schema from the same Pydantic model used locally."""
    raw = AIAnalysisSchema.model_json_schema()
    # Prefer $defs naming used by OpenAI structured outputs.
    if "definitions" in raw and "$defs" not in raw:
        raw["$defs"] = raw.pop("definitions")
    return _ensure_strict_object(raw)


def openai_response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "incident_triage_analysis",
            "strict": True,
            "schema": analysis_json_schema(),
        },
    }


def analyze_with_openai(
    evidence_pack: dict[str, Any],
    *,
    client_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Call OpenAI with strict Structured Outputs and return a locally validated dict.

    Raises distinct error types for refusal, incomplete, provider, and schema failures.
    Never logs or returns the API key.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise AIProviderError("OPENAI_API_KEY not configured")

    if client_factory is not None:
        client = client_factory()
    else:
        from openai import OpenAI

        timeout = float(os.environ.get("OPENAI_TIMEOUT_SEC", "45"))
        client = OpenAI(api_key=api_key, timeout=timeout)

    user_content = (
        "Evidence pack (JSON):\n"
        + json.dumps(evidence_pack, indent=2)
        + "\n\nProduce an incident triage analysis. "
        "observed_evidence MUST be an array of objects with keys "
        "id, evidence_type, summary. Do not nest threshold_alarms/"
        "statistical_anomalies objects under observed_evidence."
    )

    try:
        response = client.chat.completions.create(
            model=model_name(),
            temperature=0.2,
            response_format=openai_response_format(),
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        )
    except AIAnalysisError:
        raise
    except Exception as exc:  # noqa: BLE001 - map SDK/network failures
        logger.exception("OpenAI provider request failed")
        raise AIProviderError(f"AI provider failure: {exc}") from exc

    if not getattr(response, "choices", None):
        raise AIIncompleteError("AI provider returned no choices")

    choice = response.choices[0]
    finish_reason = getattr(choice, "finish_reason", None)
    message = choice.message

    refusal = getattr(message, "refusal", None)
    if refusal:
        raise AIRefusalError(f"Model refused to analyze: {refusal}")

    if finish_reason in {"length", "content_filter"}:
        raise AIIncompleteError(f"Incomplete AI response (finish_reason={finish_reason})")

    content = getattr(message, "content", None)
    if not content:
        raise AIIncompleteError("Incomplete AI response (empty content)")

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise AISchemaValidationError(f"AI response was not valid JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise AISchemaValidationError("AI response root must be a JSON object")

    validated = validate_analysis(parsed)
    return validated.model_dump()
