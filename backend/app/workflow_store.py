from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .models import AuditReport, GeneratePitchRequest, MarketingPitch


def pitch_content_hash(pitch: MarketingPitch) -> str:
    payload = pitch.model_dump(mode="json", exclude={"pitch_hash"})
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def stamp_pitch(pitch: MarketingPitch, pitch_id: str | None = None, version: int = 1) -> MarketingPitch:
    stamped = pitch.model_copy(update={"pitch_id": pitch_id or str(uuid.uuid4()), "pitch_version": version, "pitch_hash": ""})
    return stamped.model_copy(update={"pitch_hash": pitch_content_hash(stamped)})


@dataclass
class WorkflowRecord:
    pitch: MarketingPitch
    generation_request: GeneratePitchRequest
    audit: AuditReport | None = None
    advisor_decision: str | None = None
    advisor_name: str | None = None
    decision_at: datetime | None = None
    rejection_feedback: str | None = None


def _record_payload(record: WorkflowRecord) -> dict:
    return {
        "pitch": record.pitch.model_dump(mode="json"),
        "generation_request": record.generation_request.model_dump(mode="json"),
        "audit": record.audit.model_dump(mode="json") if record.audit else None,
        "advisor_decision": record.advisor_decision,
        "advisor_name": record.advisor_name,
        "decision_at": record.decision_at.isoformat() if record.decision_at else None,
        "rejection_feedback": record.rejection_feedback,
    }


def _record_from_payload(item: dict) -> WorkflowRecord:
    return WorkflowRecord(
        pitch=MarketingPitch.model_validate(item["pitch"]),
        generation_request=GeneratePitchRequest.model_validate(item["generation_request"]),
        audit=AuditReport.model_validate(item["audit"]) if item.get("audit") else None,
        advisor_decision=item.get("advisor_decision"),
        advisor_name=item.get("advisor_name"),
        decision_at=datetime.fromisoformat(item["decision_at"]) if item.get("decision_at") else None,
        rejection_feedback=item.get("rejection_feedback"),
    )


class WorkflowStore:
    """Use Postgres in production and the existing JSON store locally."""

    def __init__(self, *, database_url: str | None = None, path: Path | None = None) -> None:
        self._records: dict[str, WorkflowRecord] = {}
        self._lock = threading.RLock()
        self._database_url = database_url if database_url is not None else os.getenv("DATABASE_URL")
        configured_path = os.getenv("WORKFLOW_STORE_PATH")
        self._path = path or (
            Path(configured_path).expanduser().resolve()
            if configured_path
            else Path(__file__).resolve().parents[1] / "data" / "pitch_workflows.json"
        )
        if self._database_url:
            self._ensure_schema()
        else:
            self._load_file()

    @property
    def backend(self) -> str:
        return "postgres" if self._database_url else "json"

    def _connect(self):
        import psycopg

        return psycopg.connect(self._database_url, connect_timeout=10)

    def _ensure_schema(self) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS pitch_workflows (
                    pitch_id TEXT PRIMARY KEY,
                    payload JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            # The table lives in Supabase's API-exposed public schema, but only
            # the server-side Postgres connection may access workflow records.
            cursor.execute("ALTER TABLE pitch_workflows ENABLE ROW LEVEL SECURITY")
            cursor.execute("REVOKE ALL ON TABLE pitch_workflows FROM anon, authenticated")

    def _load_file(self) -> None:
        if not self._path.exists():
            return
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            self._records = {pitch_id: _record_from_payload(item) for pitch_id, item in payload.items()}
        except (OSError, ValueError, KeyError):
            self._records = {}

    def _read_postgres(self, pitch_id: str) -> WorkflowRecord:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT payload FROM pitch_workflows WHERE pitch_id = %s", (pitch_id,))
            row = cursor.fetchone()
        if not row:
            raise KeyError(pitch_id)
        payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        return _record_from_payload(payload)

    def _write_postgres(self, pitch_id: str, record: WorkflowRecord) -> None:
        payload = json.dumps(_record_payload(record), ensure_ascii=False)
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO pitch_workflows (pitch_id, payload, updated_at)
                VALUES (%s, %s::jsonb, NOW())
                ON CONFLICT (pitch_id) DO UPDATE
                SET payload = EXCLUDED.payload, updated_at = NOW()
                """,
                (pitch_id, payload),
            )

    def _persist_file(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {pitch_id: _record_payload(record) for pitch_id, record in self._records.items()}
        temporary = self._path.with_suffix(f"{self._path.suffix}.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self._path)

    def _save(self, pitch_id: str, record: WorkflowRecord) -> None:
        if self._database_url:
            self._write_postgres(pitch_id, record)
        else:
            self._records[pitch_id] = record
            self._persist_file()

    def save_new(self, pitch: MarketingPitch, request: GeneratePitchRequest) -> MarketingPitch:
        stamped = stamp_pitch(pitch)
        with self._lock:
            self._save(stamped.pitch_id, WorkflowRecord(stamped, deepcopy(request)))
        return stamped

    def restore(self, pitch: MarketingPitch, request: GeneratePitchRequest) -> MarketingPitch:
        if not pitch.pitch_id:
            return self.save_new(pitch, request)
        with self._lock:
            self._save(pitch.pitch_id, WorkflowRecord(pitch, deepcopy(request)))
        return pitch

    def get(self, pitch_id: str) -> WorkflowRecord:
        with self._lock:
            if self._database_url:
                return self._read_postgres(pitch_id)
            record = self._records.get(pitch_id)
            if not record:
                raise KeyError(pitch_id)
            return record

    def save_version(self, pitch_id: str, pitch: MarketingPitch) -> MarketingPitch:
        with self._lock:
            record = self.get(pitch_id)
            stamped = stamp_pitch(pitch, pitch_id=pitch_id, version=record.pitch.pitch_version + 1)
            record.pitch = stamped
            record.audit = None
            record.advisor_decision = None
            record.advisor_name = None
            record.decision_at = None
            self._save(pitch_id, record)
            return stamped

    def save_audit(self, pitch_id: str, report: AuditReport) -> AuditReport:
        with self._lock:
            record = self.get(pitch_id)
            record.audit = report
            self._save(pitch_id, record)
            return report

    def approve(self, pitch_id: str, advisor_name: str) -> WorkflowRecord:
        with self._lock:
            record = self.get(pitch_id)
            record.advisor_decision = "approve"
            record.advisor_name = advisor_name
            record.decision_at = datetime.now(timezone.utc)
            self._save(pitch_id, record)
            return record

    def reject(self, pitch_id: str, advisor_name: str, feedback: str) -> WorkflowRecord:
        with self._lock:
            record = self.get(pitch_id)
            record.advisor_decision = "reject"
            record.advisor_name = advisor_name
            record.decision_at = datetime.now(timezone.utc)
            record.rejection_feedback = feedback
            self._save(pitch_id, record)
            return record


workflow_store = WorkflowStore()
