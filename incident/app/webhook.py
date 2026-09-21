"""Parse Alertmanager webhook payloads into normalized alarms."""

from __future__ import annotations

from typing import Any

REQUIRED_LABELS = ("alertname", "severity", "site", "sector")
ALLOWED_SEVERITY = {"warning", "major", "critical"}


class PayloadError(ValueError):
    pass


def parse_webhook(body: Any) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(body, dict):
        raise PayloadError("Webhook body must be a JSON object")
    alerts = body.get("alerts")
    if not isinstance(alerts, list):
        raise PayloadError("Webhook body must include an alerts array")

    parsed: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, alert in enumerate(alerts):
        if not isinstance(alert, dict):
            errors.append(f"alerts[{index}] is not an object")
            continue
        labels = alert.get("labels") or {}
        if not isinstance(labels, dict):
            errors.append(f"alerts[{index}] labels must be an object")
            continue
        missing = [name for name in REQUIRED_LABELS if not labels.get(name)]
        if missing:
            errors.append(f"alerts[{index}] missing labels: {', '.join(missing)}")
            continue
        severity = str(labels["severity"])
        if severity not in ALLOWED_SEVERITY:
            errors.append(f"alerts[{index}] invalid severity '{severity}'")
            continue
        fingerprint = alert.get("fingerprint") or ""
        if not fingerprint:
            errors.append(f"alerts[{index}] missing fingerprint")
            continue
        annotations = alert.get("annotations") or {}
        if not isinstance(annotations, dict):
            annotations = {}
        status = alert.get("status") or "firing"
        if status not in {"firing", "resolved"}:
            errors.append(f"alerts[{index}] invalid status '{status}'")
            continue
        parsed.append(
            {
                "fingerprint": str(fingerprint),
                "alertname": str(labels["alertname"]),
                "severity": severity,
                "site": str(labels["site"]),
                "sector": str(labels["sector"]),
                "domain": str(labels.get("domain") or ""),
                "status": status,
                "affected_kpi": annotations.get("kpi") or labels.get("kpi"),
                "starts_at": alert.get("startsAt"),
                "ends_at": alert.get("endsAt"),
                "labels": labels,
                "annotations": annotations,
            }
        )
    return parsed, errors
