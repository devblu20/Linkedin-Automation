"""SQLAlchemy repository supporting local SQLite and Neon PostgreSQL."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    select,
    text,
)
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.pool import NullPool

from linkedin_automation.domain.enums import OutreachStatus, RunStatus
from linkedin_automation.domain.models import (
    ConnectionRequest,
    Lead,
    MatchEvidence,
    Observation,
    OutreachRecord,
    ResearchRun,
    RunCounters,
    SentMessage,
)
from linkedin_automation.domain.services import merge_duplicate


class Base(DeclarativeBase):
    """Declarative base shared with Alembic metadata."""


class RunRow(Base):
    __tablename__ = "research_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    search_name: Mapped[str] = mapped_column(String(255))
    schema_version: Mapped[int] = mapped_column(Integer)
    definition_json: Mapped[str] = mapped_column(Text)
    definition_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    dry_run: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    observed: Mapped[int] = mapped_column(Integer, default=0)
    accepted: Mapped[int] = mapped_column(Integer, default=0)
    rejected: Mapped[int] = mapped_column(Integer, default=0)
    duplicated: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    checkpoint_page: Mapped[int] = mapped_column(Integer, default=0)
    report_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    drive_file_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    drive_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class ObservationRow(Base):
    __tablename__ = "observations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("research_runs.id", ondelete="CASCADE"), index=True
    )
    candidate_json: Mapped[str] = mapped_column(Text)
    accepted: Mapped[bool] = mapped_column(Boolean)
    canonical_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class LeadRow(Base):
    __tablename__ = "leads"
    __table_args__ = (UniqueConstraint("run_id", "profile_url", name="uq_run_profile"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("research_runs.id", ondelete="CASCADE"), index=True
    )
    profile_url: Mapped[str] = mapped_column(Text)
    full_name: Mapped[str] = mapped_column(String(255))
    headline: Mapped[str] = mapped_column(Text)
    current_title: Mapped[str] = mapped_column(String(255))
    company: Mapped[str] = mapped_column(String(255))
    location: Mapped[str] = mapped_column(String(255))
    industry: Mapped[str] = mapped_column(String(255))
    company_size_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    company_size_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    self_employed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    evidence_json: Mapped[str] = mapped_column(Text)
    source_search: Mapped[str] = mapped_column(Text)
    first_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str] = mapped_column(Text)


class OutreachRow(Base):
    __tablename__ = "outreach_records"

    lead_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("leads.id", ondelete="CASCADE"), primary_key=True
    )
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("research_runs.id", ondelete="CASCADE"), index=True
    )
    firm_type: Mapped[str] = mapped_column(String(255), default="")
    funds_gbp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fund_evidence_url: Mapped[str] = mapped_column(Text, default="")
    fund_evidence_notes: Mapped[str] = mapped_column(Text, default="")
    score: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), index=True)
    connection_message: Mapped[str] = mapped_column(Text, default="")
    follow_up_message: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ConnectionRequestRow(Base):
    __tablename__ = "connection_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    lead_id: Mapped[str] = mapped_column(String(36), ForeignKey("leads.id"), index=True)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("research_runs.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str] = mapped_column(Text, default="")


class SentMessageRow(Base):
    __tablename__ = "messages_sent"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    lead_id: Mapped[str] = mapped_column(String(36), ForeignKey("leads.id"), index=True)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("research_runs.id"), index=True)
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), index=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    error_message: Mapped[str] = mapped_column(Text, default="")


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _run_from_row(row: RunRow) -> ResearchRun:
    return ResearchRun(
        id=UUID(row.id),
        search_name=row.search_name,
        schema_version=row.schema_version,
        definition_json=row.definition_json,
        definition_hash=row.definition_hash,
        status=RunStatus(row.status),
        dry_run=row.dry_run,
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
        counters=RunCounters(
            observed=row.observed,
            accepted=row.accepted,
            rejected=row.rejected,
            duplicated=row.duplicated,
            failed=row.failed,
        ),
        checkpoint_page=row.checkpoint_page,
        report_path=row.report_path,
        drive_file_id=row.drive_file_id,
        drive_url=row.drive_url,
        error_category=row.error_category,
        error_message=row.error_message,
    )


def _lead_from_row(row: LeadRow) -> Lead:
    evidence = json.loads(row.evidence_json)
    return Lead(
        id=UUID(row.id),
        run_id=UUID(row.run_id),
        profile_url=row.profile_url,
        full_name=row.full_name,
        headline=row.headline,
        current_title=row.current_title,
        company=row.company,
        location=row.location,
        industry=row.industry,
        evidence=MatchEvidence(
            matched=tuple(evidence["matched"]), excluded=tuple(evidence["excluded"])
        ),
        source_search=row.source_search,
        first_observed_at=_aware(row.first_observed_at),
        last_observed_at=_aware(row.last_observed_at),
        notes=row.notes,
        company_size_min=row.company_size_min,
        company_size_max=row.company_size_max,
        self_employed=row.self_employed,
    )


def _outreach_from_row(row: OutreachRow) -> OutreachRecord:
    return OutreachRecord(
        lead_id=UUID(row.lead_id),
        run_id=UUID(row.run_id),
        firm_type=row.firm_type,
        funds_gbp=row.funds_gbp,
        fund_evidence_url=row.fund_evidence_url,
        fund_evidence_notes=row.fund_evidence_notes,
        score=row.score,
        status=OutreachStatus(row.status),
        connection_message=row.connection_message,
        follow_up_message=row.follow_up_message,
        updated_at=_aware(row.updated_at),
    )


class DatabaseResearchRepository:
    """SQLAlchemy repository with deterministic per-run upserts."""

    def __init__(self, database: str | Path) -> None:
        if isinstance(database, Path) or "://" not in str(database):
            path = Path(database).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            self._database_url = f"sqlite:///{path.as_posix()}"
            self._path: Path | None = path
        else:
            self._database_url = str(database)
            self._path = None
        if self._database_url.startswith("postgresql://"):
            self._database_url = self._database_url.replace(
                "postgresql://", "postgresql+psycopg://", 1
            )
        url = make_url(self._database_url)
        if url.get_backend_name() not in {"sqlite", "postgresql"}:
            raise ValueError("database must be a SQLite path/URL or PostgreSQL URL")
        self._engine = create_engine(self._database_url, pool_pre_ping=True, poolclass=NullPool)

    def initialize(self) -> None:
        Base.metadata.create_all(self._engine)
        # Databases bootstrapped by the local app represent the initial migration revision.
        # Recording it keeps later Alembic upgrades compatible with automatic local setup.
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS alembic_version "
                    "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
                )
            )
            current = connection.scalar(text("SELECT version_num FROM alembic_version LIMIT 1"))
            if current is None:
                connection.execute(
                    text("INSERT INTO alembic_version (version_num) VALUES ('0003')")
                )
            elif current in {"0001", "0002"}:
                connection.execute(text("UPDATE alembic_version SET version_num = '0003'"))

    def database_location(self) -> str:
        """Return a password-safe description of the configured database."""
        if self._path is not None:
            return self._database_url
        return str(make_url(self._database_url).set(password="***"))

    def close(self) -> None:
        """Release database resources deterministically."""
        self._engine.dispose()

    def add_run(self, run: ResearchRun) -> None:
        with Session(self._engine) as session:
            session.add(self._new_run_row(run))
            session.commit()

    def save_run(self, run: ResearchRun) -> None:
        with Session(self._engine) as session:
            row = session.get(RunRow, str(run.id))
            if row is None:
                raise KeyError(f"run not found: {run.id}")
            self._copy_run(row, run)
            session.commit()

    def get_run(self, run_id: UUID) -> ResearchRun | None:
        with Session(self._engine) as session:
            row = session.get(RunRow, str(run_id))
            return _run_from_row(row) if row is not None else None

    def add_observation(self, observation: Observation) -> None:
        candidate = observation.candidate
        payload = {
            "profile_url": candidate.profile_url,
            "full_name": candidate.full_name,
            "headline": candidate.headline,
            "current_title": candidate.current_title,
            "company": candidate.company,
            "location": candidate.location,
            "industry": candidate.industry,
            "company_size_min": candidate.company_size_min,
            "company_size_max": candidate.company_size_max,
            "self_employed": candidate.self_employed,
            "source_search": candidate.source_search,
            "collected_at": candidate.collected_at.isoformat(),
        }
        with Session(self._engine) as session:
            session.add(
                ObservationRow(
                    id=str(observation.id),
                    run_id=str(observation.run_id),
                    candidate_json=json.dumps(payload, sort_keys=True),
                    accepted=observation.accepted,
                    canonical_url=observation.canonical_url,
                    rejection_reason=observation.rejection_reason,
                    collected_at=observation.candidate.collected_at,
                )
            )
            session.commit()

    def upsert_lead(self, lead: Lead) -> bool:
        """Insert a lead or merge it into an existing URL; return True when inserted."""
        with Session(self._engine) as session:
            existing_row = session.scalar(
                select(LeadRow).where(
                    LeadRow.run_id == str(lead.run_id), LeadRow.profile_url == lead.profile_url
                )
            )
            if existing_row is None:
                session.add(self._new_lead_row(lead))
                inserted = True
            else:
                merged = merge_duplicate(_lead_from_row(existing_row), lead)
                self._copy_lead(existing_row, merged)
                inserted = False
            session.commit()
            return inserted

    def list_leads(self, run_id: UUID) -> list[Lead]:
        with Session(self._engine) as session:
            rows = session.scalars(
                select(LeadRow).where(LeadRow.run_id == str(run_id)).order_by(LeadRow.profile_url)
            ).all()
            return [_lead_from_row(row) for row in rows]

    def get_lead(self, lead_id: UUID) -> Lead | None:
        with Session(self._engine) as session:
            row = session.get(LeadRow, str(lead_id))
            return _lead_from_row(row) if row is not None else None

    def upsert_outreach(self, record: OutreachRecord) -> None:
        with Session(self._engine) as session:
            row = session.get(OutreachRow, str(record.lead_id))
            if row is None:
                row = OutreachRow(lead_id=str(record.lead_id), run_id=str(record.run_id))
                session.add(row)
            row.firm_type = record.firm_type
            row.funds_gbp = record.funds_gbp
            row.fund_evidence_url = record.fund_evidence_url
            row.fund_evidence_notes = record.fund_evidence_notes
            row.score = record.score
            row.status = record.status.value
            row.connection_message = record.connection_message
            row.follow_up_message = record.follow_up_message
            row.updated_at = record.updated_at
            session.commit()

    def get_outreach(self, lead_id: UUID) -> OutreachRecord | None:
        with Session(self._engine) as session:
            row = session.get(OutreachRow, str(lead_id))
            return _outreach_from_row(row) if row is not None else None

    def list_outreach(self, run_id: UUID) -> list[OutreachRecord]:
        with Session(self._engine) as session:
            rows = session.scalars(
                select(OutreachRow)
                .where(OutreachRow.run_id == str(run_id))
                .order_by(OutreachRow.score.desc(), OutreachRow.lead_id)
            ).all()
            return [_outreach_from_row(row) for row in rows]

    def count_observations_since(self, since: datetime) -> int:
        with Session(self._engine) as session:
            value = session.scalar(
                select(func.count())
                .select_from(ObservationRow)
                .where(ObservationRow.collected_at >= since)
            )
            return int(value or 0)

    def add_connection_request(self, request: ConnectionRequest) -> None:
        with Session(self._engine) as session:
            session.add(ConnectionRequestRow(
                id=str(request.id), lead_id=str(request.lead_id), run_id=str(request.run_id),
                status=request.status, requested_at=request.requested_at,
                updated_at=request.updated_at, error_message=request.error_message,
            ))
            session.commit()

    def list_connection_requests(self, run_id: UUID) -> list[ConnectionRequest]:
        with Session(self._engine) as session:
            rows = session.scalars(select(ConnectionRequestRow).where(
                ConnectionRequestRow.run_id == str(run_id)
            ).order_by(ConnectionRequestRow.requested_at.desc())).all()
            return [ConnectionRequest(UUID(r.id), UUID(r.lead_id), UUID(r.run_id), r.status,
                _aware(r.requested_at), _aware(r.updated_at), r.error_message) for r in rows]

    def count_connection_requests_since(self, since: datetime) -> int:
        with Session(self._engine) as session:
            return int(session.scalar(select(func.count()).select_from(ConnectionRequestRow).where(
                ConnectionRequestRow.requested_at >= since,
                ConnectionRequestRow.status == "sent",
            )) or 0)

    def add_sent_message(self, message: SentMessage) -> None:
        with Session(self._engine) as session:
            session.add(SentMessageRow(
                id=str(message.id), lead_id=str(message.lead_id), run_id=str(message.run_id),
                content=message.content, status=message.status, sent_at=message.sent_at,
                error_message=message.error_message,
            ))
            session.commit()

    def list_sent_messages(self, run_id: UUID) -> list[SentMessage]:
        with Session(self._engine) as session:
            rows = session.scalars(select(SentMessageRow).where(
                SentMessageRow.run_id == str(run_id)
            ).order_by(SentMessageRow.sent_at.desc())).all()
            return [SentMessage(UUID(r.id), UUID(r.lead_id), UUID(r.run_id), r.content,
                r.status, _aware(r.sent_at), r.error_message) for r in rows]

    def count_sent_messages_since(self, since: datetime) -> int:
        with Session(self._engine) as session:
            return int(session.scalar(select(func.count()).select_from(SentMessageRow).where(
                SentMessageRow.sent_at >= since, SentMessageRow.status == "sent"
            )) or 0)

    @staticmethod
    def _new_run_row(run: ResearchRun) -> RunRow:
        row = RunRow(id=str(run.id))
        DatabaseResearchRepository._copy_run(row, run)
        return row

    @staticmethod
    def _copy_run(row: RunRow, run: ResearchRun) -> None:
        row.search_name = run.search_name
        row.schema_version = run.schema_version
        row.definition_json = run.definition_json
        row.definition_hash = run.definition_hash
        row.status = run.status.value
        row.dry_run = run.dry_run
        row.created_at = run.created_at
        row.updated_at = run.updated_at
        row.observed = run.counters.observed
        row.accepted = run.counters.accepted
        row.rejected = run.counters.rejected
        row.duplicated = run.counters.duplicated
        row.failed = run.counters.failed
        row.checkpoint_page = run.checkpoint_page
        row.report_path = run.report_path
        row.drive_file_id = run.drive_file_id
        row.drive_url = run.drive_url
        row.error_category = run.error_category
        row.error_message = run.error_message

    @staticmethod
    def _new_lead_row(lead: Lead) -> LeadRow:
        row = LeadRow(id=str(lead.id), run_id=str(lead.run_id), profile_url=lead.profile_url)
        DatabaseResearchRepository._copy_lead(row, lead)
        return row

    @staticmethod
    def _copy_lead(row: LeadRow, lead: Lead) -> None:
        row.full_name = lead.full_name
        row.headline = lead.headline
        row.current_title = lead.current_title
        row.company = lead.company
        row.location = lead.location
        row.industry = lead.industry
        row.company_size_min = lead.company_size_min
        row.company_size_max = lead.company_size_max
        row.self_employed = lead.self_employed
        row.evidence_json = json.dumps(
            {"matched": lead.evidence.matched, "excluded": lead.evidence.excluded},
            sort_keys=True,
        )
        row.source_search = lead.source_search
        row.first_observed_at = lead.first_observed_at
        row.last_observed_at = lead.last_observed_at
        row.notes = lead.notes


# Backward-compatible name for callers and tests that still construct a local SQLite repository.
SqliteResearchRepository = DatabaseResearchRepository
