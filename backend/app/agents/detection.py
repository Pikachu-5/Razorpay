from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database.models import DetectorState, Payment
from app.database.session import session_factory


@dataclass
class SegmentStats:
    method: str | None
    bank: str | None
    recent_attempts: int = 0
    recent_failures: int = 0
    baseline_attempts: int = 0
    baseline_failures: int = 0
    revenue_at_risk_minor: int = 0

    @property
    def recent_failure_rate(self) -> float:
        return self.recent_failures / self.recent_attempts if self.recent_attempts else 0.0

    @property
    def baseline_failure_rate(self) -> float:
        if self.baseline_attempts >= MIN_BASELINE_ATTEMPTS:
            return self.baseline_failures / self.baseline_attempts
        return 0.12

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "bank": self.bank,
            "recent_attempts": self.recent_attempts,
            "recent_failures": self.recent_failures,
            "recent_failure_rate": round(self.recent_failure_rate, 4),
            "baseline_failure_rate": round(self.baseline_failure_rate, 4),
            "revenue_at_risk_minor": self.revenue_at_risk_minor,
        }


@dataclass
class Alarm:
    segment: SegmentStats
    detectors_fired: list[str] = field(default_factory=list)
    z_score: float = 0.0
    cusum: float = 0.0
    severity: str = "medium"


RECENT_WINDOW_MINUTES = 10
BASELINE_WINDOW_HOURS = 24
MIN_RECENT_ATTEMPTS = 15
MIN_BASELINE_ATTEMPTS = 40
RATE_LIFT = 1.7
ABSOLUTE_RATE_FLOOR = 0.35
EXCESS_FLOOR = 0.20
EWMA_ALPHA = 0.15
CUSUM_DRIFT = 0.02
CUSUM_THRESHOLD = 0.35

# `method` and `bank` are nullable on payments but form the detector's primary
# key, so NULL is stored as an explicit sentinel rather than a SQL NULL (which
# never matches itself in ON CONFLICT).
_NULL_SEGMENT = "␀"


def _segment_key(method: str | None, bank: str | None) -> tuple[str, str]:
    return (method or _NULL_SEGMENT, bank or _NULL_SEGMENT)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def update_online_detectors(
    method: str | None,
    bank: str | None,
    failed: bool,
    baseline_rate: float = 0.12,
) -> dict[str, float]:
    """Advance the sequential detectors for one segment, durably.

    The read-modify-write happens inside a single INSERT ... ON CONFLICT DO
    UPDATE so concurrent API replicas processing different payments for the
    same bank cannot lose each other's observations. In the SET clause a bare
    column refers to the existing row and `excluded` to the proposed one.
    """
    key_method, key_bank = _segment_key(method, bank)
    x = 1.0 if failed else 0.0
    seed_ewma = EWMA_ALPHA * x + (1 - EWMA_ALPHA) * baseline_rate
    seed_cusum = max(0.0, x - baseline_rate - CUSUM_DRIFT)

    statement = (
        pg_insert(DetectorState)
        .values(
            method=key_method,
            bank=key_bank,
            ewma=seed_ewma,
            cusum=seed_cusum,
            observations=1,
            updated_at=_utcnow(),
        )
        .on_conflict_do_update(
            index_elements=["method", "bank"],
            set_={
                "ewma": EWMA_ALPHA * x + (1 - EWMA_ALPHA) * DetectorState.ewma,
                "cusum": func.greatest(
                    0.0, DetectorState.cusum + (x - baseline_rate - CUSUM_DRIFT)
                ),
                "observations": DetectorState.observations + 1,
                "updated_at": _utcnow(),
            },
        )
        .returning(DetectorState.ewma, DetectorState.cusum)
    )
    async with session_factory() as session:
        row = (await session.execute(statement)).first()
        await session.commit()
    return {"ewma": float(row[0]), "cusum": float(row[1])} if row else {"ewma": 0.0, "cusum": 0.0}


async def load_cusum_state() -> dict[tuple[str, str], float]:
    """Read every segment's CUSUM in one query, for a single detector sweep."""
    async with session_factory() as session:
        rows = (
            await session.execute(select(DetectorState.method, DetectorState.bank, DetectorState.cusum))
        ).all()
    return {(method, bank): float(cusum) for method, bank, cusum in rows}


async def cusum_alarm(method: str | None, bank: str | None) -> bool:
    state = await load_cusum_state()
    return state.get(_segment_key(method, bank), 0.0) >= CUSUM_THRESHOLD


async def collect_segment_stats() -> list[SegmentStats]:
    now = _utcnow()
    recent_since = now - timedelta(minutes=RECENT_WINDOW_MINUTES)
    baseline_since = now - timedelta(hours=BASELINE_WINDOW_HOURS)

    async with session_factory() as session:
        failure_amount = case(
            (Payment.status == "failed", Payment.amount_minor), else_=0
        )
        recent_rows = (
            await session.execute(
                select(
                    Payment.method,
                    Payment.bank,
                    func.count().label("attempts"),
                    func.sum(case((Payment.status == "failed", 1), else_=0)).label("failures"),
                    func.sum(failure_amount).label("risk"),
                )
                .where(Payment.occurred_at >= recent_since)
                .group_by(Payment.method, Payment.bank)
            )
        ).all()

        baseline_rows = (
            await session.execute(
                select(
                    Payment.method,
                    Payment.bank,
                    func.count().label("attempts"),
                    func.sum(case((Payment.status == "failed", 1), else_=0)).label("failures"),
                )
                .where(Payment.occurred_at >= baseline_since)
                .where(Payment.occurred_at < recent_since)
                .group_by(Payment.method, Payment.bank)
            )
        ).all()

    baseline_map = {
        (m, b): (attempts, failures) for m, b, attempts, failures in baseline_rows
    }
    segments: list[SegmentStats] = []
    for method, bank, attempts, failures, risk in recent_rows:
        base_attempts, base_failures = baseline_map.get((method, bank), (0, 0))
        segments.append(
            SegmentStats(
                method=method,
                bank=bank,
                recent_attempts=int(attempts or 0),
                recent_failures=int(failures or 0),
                baseline_attempts=int(base_attempts),
                baseline_failures=int(base_failures),
                revenue_at_risk_minor=int(risk or 0),
            )
        )
    return segments


def evaluate_segment(
    segment: SegmentStats, cusum_state: dict[tuple[str, str], float] | None = None
) -> Alarm | None:
    if segment.recent_attempts < MIN_RECENT_ATTEMPTS:
        return None
    state = cusum_state or {}
    cusum = state.get(_segment_key(segment.method, segment.bank), 0.0)

    fired: list[str] = []
    base = segment.baseline_failure_rate
    recent = segment.recent_failure_rate

    lift_ok = recent >= max(ABSOLUTE_RATE_FLOOR, base * RATE_LIFT)
    excess_ok = (recent - base) >= EXCESS_FLOOR
    if lift_ok and excess_ok:
        fired.append("window_baseline")

    n = segment.recent_attempts
    se = math.sqrt(max(base * (1 - base), 1e-6) / n)
    z = (recent - base) / se if se > 0 else 0.0
    if z >= 5.0 and recent > base:
        fired.append("z_score")

    if cusum >= CUSUM_THRESHOLD:
        fired.append("cusum")

    if not fired:
        return None
    if "window_baseline" not in fired and recent < ABSOLUTE_RATE_FLOOR:
        return None

    severity = "high" if recent - base >= 0.45 or segment.revenue_at_risk_minor >= 50_000_000 else "medium"
    return Alarm(
        segment=segment,
        detectors_fired=fired,
        z_score=round(z, 2),
        cusum=round(cusum, 3),
        severity=severity,
    )


async def scan_for_anomalies() -> list[Alarm]:
    segments = await collect_segment_stats()
    # One read for the whole sweep rather than a query per segment.
    cusum_state = await load_cusum_state()
    alarms = []
    for segment in segments:
        alarm = evaluate_segment(segment, cusum_state)
        if alarm is not None:
            alarms.append(alarm)
    alarms.sort(key=lambda a: a.segment.revenue_at_risk_minor, reverse=True)
    return alarms
