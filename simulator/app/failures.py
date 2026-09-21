"""Active incident store for failure injection."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from app.config import SECTORS, SITES
from app.models import FailureType, Incident
from app.simulator import NetworkSimulator


class IncidentStore:
    def __init__(self) -> None:
        self._incidents: dict[str, Incident] = {}

    def create(
        self,
        failure_type: FailureType,
        site: str,
        sector: Optional[str],
    ) -> Incident:
        if site not in SITES:
            raise ValueError(f"Unknown site '{site}'. Valid: {', '.join(SITES)}")
        if sector is not None and sector not in SECTORS:
            raise ValueError(
                f"Unknown sector '{sector}'. Valid: {', '.join(SECTORS)}"
            )

        operating_state = NetworkSimulator.FAILURE_STATE_MAP[failure_type]
        incident = Incident(
            id=str(uuid.uuid4()),
            failure_type=failure_type,
            site=site,
            sector=sector,
            created_at=datetime.now(timezone.utc),
            operating_state=operating_state,
        )
        self._incidents[incident.id] = incident
        return incident

    def get(self, failure_id: str) -> Incident | None:
        return self._incidents.get(failure_id)

    def list(self) -> list[Incident]:
        return sorted(self._incidents.values(), key=lambda i: i.created_at)

    def delete(self, failure_id: str) -> bool:
        return self._incidents.pop(failure_id, None) is not None

    def clear(self) -> int:
        count = len(self._incidents)
        self._incidents.clear()
        return count

    def failures_for(self, site: str, sector: str) -> list[FailureType]:
        matches: list[FailureType] = []
        for incident in self._incidents.values():
            if incident.site != site:
                continue
            if incident.sector is None or incident.sector == sector:
                matches.append(incident.failure_type)
        # Deduplicate while preserving order
        seen: set[FailureType] = set()
        unique: list[FailureType] = []
        for failure in matches:
            if failure not in seen:
                seen.add(failure)
                unique.append(failure)
        return unique

    def count(self) -> int:
        return len(self._incidents)
