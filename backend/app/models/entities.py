from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Flight(Base):
    __tablename__ = "flights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="scheduled")


class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    booking_ref: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    passenger_name: Mapped[str] = mapped_column(String(120))
    flight_ids: Mapped[list] = mapped_column(JSON)
    itinerary: Mapped[list] = mapped_column(JSON, nullable=False, default=list, server_default="[]")
    passenger_constraints: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")


class DisruptionEvent(Base):
    __tablename__ = "disruption_events"
    __table_args__ = (UniqueConstraint("event_id", name="uq_disruption_event_event_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    fid: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class DisruptionCase(Base):
    __tablename__ = "disruption_cases"
    __table_args__ = (UniqueConstraint("event_id", "booking_id", name="uq_case_event_booking"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("disruption_events.event_id"), nullable=False)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"), nullable=False)
    flight_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="open", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class RecoveryDecision(Base):
    __tablename__ = "recovery_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("disruption_cases.id"), nullable=False, index=True)
    policy_id: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    decision_code: Mapped[str] = mapped_column(String(40), nullable=False)
    explanation: Mapped[str] = mapped_column(String(500), nullable=False)
    reason_codes: Mapped[list] = mapped_column(JSON, nullable=False)
    requires_human_approval: Mapped[bool] = mapped_column(default=True, nullable=False)
    candidates: Mapped[list] = mapped_column(JSON, nullable=False)
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class RecoveryAction(Base):
    __tablename__ = "recovery_actions"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_recovery_action_idempotency_key"),)

    action_id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid4().hex)
    case_id: Mapped[int] = mapped_column(ForeignKey("disruption_cases.id"), nullable=False, index=True)
    decision_id: Mapped[int] = mapped_column(ForeignKey("recovery_decisions.id"), nullable=False)
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    proposed_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING_APPROVAL")
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    executed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[Optional[str]] = mapped_column(String(120))
    execution_result: Mapped[Optional[dict]] = mapped_column(JSON)
    failure_reason: Mapped[Optional[str]] = mapped_column(String(500))


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
