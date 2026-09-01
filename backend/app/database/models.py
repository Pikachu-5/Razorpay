from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.session import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RawEvent(Base):
    __tablename__ = "raw_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    event_uid: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    event_type: Mapped[str | None] = mapped_column(String(100), index=True)
    entity_type: Mapped[str | None] = mapped_column(String(50))
    entity_id: Mapped[str | None] = mapped_column(String(64), index=True)
    entity_ids: Mapped[list | None] = mapped_column(JSONB)
    payload: Mapped[dict] = mapped_column(JSONB)
    source: Mapped[str] = mapped_column(String(20), default="razorpay", server_default="razorpay")
    received_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default="now()"
    )
    claimed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), index=True)
    processed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)


class DurableEvent(Base):
    """Append-only monitor events shared by every API replica and SSE client."""

    __tablename__ = "durable_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(80), index=True)
    data: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default="now()", index=True
    )


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    identity_key: Mapped[str] = mapped_column(String(320), unique=True)
    email: Mapped[str | None] = mapped_column(String(320))
    contact: Mapped[str | None] = mapped_column(String(20))
    name: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default="now()")


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    razorpay_payment_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id"), nullable=True
    )
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(8), default="INR", server_default="INR")
    status: Mapped[str] = mapped_column(String(20), index=True)
    method: Mapped[str | None] = mapped_column(String(30))
    bank: Mapped[str | None] = mapped_column(String(50))
    vpa: Mapped[str | None] = mapped_column(String(120))
    card_id: Mapped[str | None] = mapped_column(String(64))
    order_id: Mapped[str | None] = mapped_column(String(64))
    invoice_id: Mapped[str | None] = mapped_column(String(64))
    subscription_id: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(24), default="razorpay_test", index=True)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    simulation_run_id: Mapped[str | None] = mapped_column(String(40), index=True)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_description: Mapped[str | None] = mapped_column(Text)
    error_source: Mapped[str | None] = mapped_column(String(40))
    error_step: Mapped[str | None] = mapped_column(String(60))
    error_reason: Mapped[str | None] = mapped_column(String(80))
    occurred_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default="now()", onupdate=_utcnow
    )


class InterventionRecord(Base):
    __tablename__ = "interventions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("opportunities.id"), index=True
    )
    action: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="executed")
    # Persisted before any external call. This makes retry workers and manual
    # re-decisions converge on the same side effect.
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    razorpay_reference: Mapped[str | None] = mapped_column(String(64), index=True)
    payload: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default="now()")


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), index=True, default="detected")
    method: Mapped[str | None] = mapped_column(String(30))
    bank: Mapped[str | None] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(200))
    severity: Mapped[str] = mapped_column(String(20), default="medium")
    diagnosis: Mapped[dict | None] = mapped_column(JSONB)
    revenue_at_risk_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    affected_failures: Mapped[int] = mapped_column(Integer, default=0)
    interventions_executed: Mapped[int] = mapped_column(Integer, default=0)
    intervention_budget: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(20), default="detector")
    detection_stats: Mapped[dict | None] = mapped_column(JSONB)
    started_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default="now()")
    resolved_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    recovered_during_incident_minor: Mapped[int] = mapped_column(BigInteger, default=0)


class DecisionAudit(Base):
    __tablename__ = "decision_audit"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("opportunities.id"), index=True
    )
    trigger: Mapped[str] = mapped_column(String(60))
    diagnosis: Mapped[dict | None] = mapped_column(JSONB)
    model_version: Mapped[str | None] = mapped_column(String(40))
    predictions: Mapped[dict | None] = mapped_column(JSONB)
    feature_snapshot: Mapped[dict | None] = mapped_column(JSONB)
    recommended_action: Mapped[str | None] = mapped_column(String(40))
    expected_recovery_minor: Mapped[int | None] = mapped_column(BigInteger)
    policy_decision: Mapped[dict | None] = mapped_column(JSONB)
    executed_action: Mapped[str | None] = mapped_column(String(40))
    execution_result: Mapped[dict | None] = mapped_column(JSONB)
    verified_outcome: Mapped[str | None] = mapped_column(String(40))
    recovered_amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default="now()")
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default="now()", onupdate=_utcnow
    )


class Opportunity(Base):
    __tablename__ = "opportunities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    payment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payments.id"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), index=True, default="open")
    category: Mapped[str] = mapped_column(String(40), default="failed_payment")
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    experiment_group: Mapped[str] = mapped_column(String(12))
    assignment_key: Mapped[str | None] = mapped_column(String(320), index=True)
    assignment_probability: Mapped[float] = mapped_column(Float, default=0.8)
    source: Mapped[str] = mapped_column(String(24), default="razorpay_test", index=True)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    simulation_run_id: Mapped[str | None] = mapped_column(String(40), index=True)
    contact_attempts: Mapped[int] = mapped_column(Integer, default=0)
    best_action: Mapped[str | None] = mapped_column(String(40))
    expected_recovery_minor: Mapped[int | None] = mapped_column(BigInteger)
    window_ends_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    closed_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default="now()")
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default="now()", onupdate=_utcnow
    )


class RazorpayOrder(Base):
    __tablename__ = "razorpay_orders"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    amount_paid_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    amount_due_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    currency: Mapped[str] = mapped_column(String(8), default="INR")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    receipt: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(24), default="razorpay_test", index=True)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    payload: Mapped[dict | None] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default="now()", onupdate=_utcnow
    )


class PaymentLinkState(Base):
    __tablename__ = "payment_link_states"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("opportunities.id"), index=True
    )
    status: Mapped[str] = mapped_column(String(24), index=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    amount_paid_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    reference_id: Mapped[str | None] = mapped_column(String(64), index=True)
    short_url: Mapped[str | None] = mapped_column(Text)
    reminder_status: Mapped[str | None] = mapped_column(String(24))
    expire_by: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    source: Mapped[str] = mapped_column(String(24), default="razorpay_test", index=True)
    payload: Mapped[dict | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default="now()", onupdate=_utcnow
    )


class PaymentDowntime(Base):
    __tablename__ = "payment_downtimes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    method: Mapped[str | None] = mapped_column(String(30), index=True)
    severity: Mapped[str] = mapped_column(String(20), default="medium")
    scheduled: Mapped[bool] = mapped_column(Boolean, default=False)
    instrument: Mapped[dict | None] = mapped_column(JSONB)
    begin_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    end_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    source: Mapped[str] = mapped_column(String(24), default="razorpay_test")
    payload: Mapped[dict | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default="now()", onupdate=_utcnow
    )


class SubscriptionState(Base):
    __tablename__ = "subscription_states"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    plan_id: Mapped[str | None] = mapped_column(String(64))
    customer_id: Mapped[str | None] = mapped_column(String(64))
    paid_count: Mapped[int] = mapped_column(Integer, default=0)
    remaining_count: Mapped[int] = mapped_column(Integer, default=0)
    current_start: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    current_end: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    source: Mapped[str] = mapped_column(String(24), default="razorpay_test", index=True)
    payload: Mapped[dict | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default="now()", onupdate=_utcnow
    )


class InvoiceState(Base):
    __tablename__ = "invoice_states"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    subscription_id: Mapped[str | None] = mapped_column(String(64), index=True)
    payment_id: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    amount_paid_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    amount_due_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    source: Mapped[str] = mapped_column(String(24), default="razorpay_test", index=True)
    payload: Mapped[dict | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default="now()", onupdate=_utcnow
    )


class RevenueAdjustment(Base):
    __tablename__ = "revenue_adjustments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    external_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    razorpay_payment_id: Mapped[str | None] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    source: Mapped[str] = mapped_column(String(24), default="razorpay_test", index=True)
    payload: Mapped[dict | None] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), default=_utcnow)


class DetectorState(Base):
    """Durable EWMA/CUSUM state for the segment-level anomaly detectors.

    These are sequential statistics: their whole purpose is to accumulate
    evidence across many payment events. Held in process memory they reset to
    zero on every deploy and diverge between API replicas, so two workers
    watching the same bank would disagree about whether it is failing. Every
    other piece of cross-replica coordination here is Postgres-backed; this is
    too.
    """

    __tablename__ = "detector_states"

    method: Mapped[str] = mapped_column(String(24), primary_key=True)
    bank: Mapped[str] = mapped_column(String(48), primary_key=True)
    ewma: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    cusum: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    observations: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=_utcnow
    )
