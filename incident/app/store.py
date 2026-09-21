"""SQLite persistence for incidents, alarms, and history."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class IncidentStore:
    def __init__(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS incidents (
                id TEXT PRIMARY KEY,
                site TEXT NOT NULL,
                sector TEXT NOT NULL,
                severity TEXT NOT NULL,
                lifecycle_state TEXT NOT NULL,
                probable_domain TEXT NOT NULL,
                first_detected TEXT NOT NULL,
                last_updated TEXT NOT NULL,
                cleared_at TEXT,
                acknowledged_at TEXT,
                acknowledged_by TEXT
            );
            CREATE TABLE IF NOT EXISTS contributing_alarms (
                id TEXT PRIMARY KEY,
                incident_id TEXT NOT NULL,
                fingerprint TEXT NOT NULL UNIQUE,
                alertname TEXT NOT NULL,
                severity TEXT NOT NULL,
                status TEXT NOT NULL,
                affected_kpi TEXT,
                starts_at TEXT,
                ends_at TEXT,
                labels_json TEXT NOT NULL,
                annotations_json TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                FOREIGN KEY (incident_id) REFERENCES incidents(id)
            );
            CREATE TABLE IF NOT EXISTS incident_events (
                id TEXT PRIMARY KEY,
                incident_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                detail_json TEXT NOT NULL,
                FOREIGN KEY (incident_id) REFERENCES incidents(id)
            );
            CREATE INDEX IF NOT EXISTS idx_incidents_open
                ON incidents(site, sector, lifecycle_state);
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def add_event(self, incident_id: str, event_type: str, detail: dict[str, Any]) -> None:
        self._conn.execute(
            """
            INSERT INTO incident_events (id, incident_id, event_type, timestamp, detail_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), incident_id, event_type, utc_now(), json.dumps(detail)),
        )

    def find_open(self, site: str, sector: str) -> Optional[sqlite3.Row]:
        return self._conn.execute(
            """
            SELECT * FROM incidents
            WHERE site = ? AND sector = ? AND lifecycle_state IN ('active', 'acknowledged')
            ORDER BY first_detected DESC LIMIT 1
            """,
            (site, sector),
        ).fetchone()

    def get_incident(self, incident_id: str) -> Optional[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM incidents WHERE id = ?", (incident_id,)
        ).fetchone()

    def insert_incident(
        self,
        *,
        site: str,
        sector: str,
        severity: str,
        probable_domain: str,
        first_detected: str,
    ) -> str:
        incident_id = str(uuid.uuid4())
        now = utc_now()
        self._conn.execute(
            """
            INSERT INTO incidents (
                id, site, sector, severity, lifecycle_state, probable_domain,
                first_detected, last_updated
            ) VALUES (?, ?, ?, ?, 'active', ?, ?, ?)
            """,
            (incident_id, site, sector, severity, probable_domain, first_detected, now),
        )
        self.add_event(
            incident_id,
            "opened",
            {"site": site, "sector": sector, "severity": severity, "domain": probable_domain},
        )
        return incident_id

    def touch(
        self,
        incident_id: str,
        *,
        severity: str | None = None,
        probable_domain: str | None = None,
        lifecycle_state: str | None = None,
        cleared_at: str | None = None,
        acknowledged_at: str | None = None,
        acknowledged_by: str | None = None,
    ) -> None:
        row = self.get_incident(incident_id)
        if row is None:
            return
        self._conn.execute(
            """
            UPDATE incidents SET
                severity = ?,
                probable_domain = ?,
                lifecycle_state = ?,
                last_updated = ?,
                cleared_at = ?,
                acknowledged_at = ?,
                acknowledged_by = ?
            WHERE id = ?
            """,
            (
                severity or row["severity"],
                probable_domain or row["probable_domain"],
                lifecycle_state or row["lifecycle_state"],
                utc_now(),
                cleared_at if cleared_at is not None else row["cleared_at"],
                acknowledged_at if acknowledged_at is not None else row["acknowledged_at"],
                acknowledged_by if acknowledged_by is not None else row["acknowledged_by"],
                incident_id,
            ),
        )

    def get_alarm_by_fingerprint(self, fingerprint: str) -> Optional[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM contributing_alarms WHERE fingerprint = ?",
            (fingerprint,),
        ).fetchone()

    def insert_alarm(self, incident_id: str, alarm: dict[str, Any]) -> str:
        alarm_id = str(uuid.uuid4())
        self._conn.execute(
            """
            INSERT INTO contributing_alarms (
                id, incident_id, fingerprint, alertname, severity, status,
                affected_kpi, starts_at, ends_at, labels_json, annotations_json, last_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                alarm_id,
                incident_id,
                alarm["fingerprint"],
                alarm["alertname"],
                alarm["severity"],
                alarm["status"],
                alarm.get("affected_kpi"),
                alarm.get("starts_at"),
                alarm.get("ends_at"),
                json.dumps(alarm.get("labels") or {}),
                json.dumps(alarm.get("annotations") or {}),
                utc_now(),
            ),
        )
        return alarm_id

    def update_alarm(self, fingerprint: str, alarm: dict[str, Any], incident_id: str | None = None) -> None:
        current = self.get_alarm_by_fingerprint(fingerprint)
        if current is None:
            return
        self._conn.execute(
            """
            UPDATE contributing_alarms SET
                incident_id = ?,
                alertname = ?,
                severity = ?,
                status = ?,
                affected_kpi = ?,
                starts_at = ?,
                ends_at = ?,
                labels_json = ?,
                annotations_json = ?,
                last_seen_at = ?
            WHERE fingerprint = ?
            """,
            (
                incident_id or current["incident_id"],
                alarm["alertname"],
                alarm["severity"],
                alarm["status"],
                alarm.get("affected_kpi"),
                alarm.get("starts_at") or current["starts_at"],
                alarm.get("ends_at"),
                json.dumps(alarm.get("labels") or {}),
                json.dumps(alarm.get("annotations") or {}),
                utc_now(),
                fingerprint,
            ),
        )

    def list_alarms(self, incident_id: str) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT * FROM contributing_alarms WHERE incident_id = ? ORDER BY starts_at",
                (incident_id,),
            )
        )

    def list_incidents(
        self,
        *,
        state: str | None = None,
        site: str | None = None,
        severity: str | None = None,
    ) -> list[sqlite3.Row]:
        clauses: list[str] = []
        params: list[str] = []
        if state:
            clauses.append("lifecycle_state = ?")
            params.append(state)
        if site:
            clauses.append("site = ?")
            params.append(site)
        if severity:
            clauses.append("severity = ?")
            params.append(severity)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return list(
            self._conn.execute(
                f"SELECT * FROM incidents {where} ORDER BY last_updated DESC",
                params,
            )
        )

    def list_events(self, incident_id: str) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT * FROM incident_events WHERE incident_id = ? ORDER BY timestamp",
                (incident_id,),
            )
        )

    def commit(self) -> None:
        self._conn.commit()

    def recent_for_metrics(self) -> list[sqlite3.Row]:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        return list(
            self._conn.execute(
                """
                SELECT * FROM incidents
                WHERE lifecycle_state IN ('active', 'acknowledged')
                   OR (lifecycle_state = 'cleared' AND cleared_at >= ?)
                """,
                (cutoff,),
            )
        )
