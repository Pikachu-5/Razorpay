from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
import uuid as uuid_lib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.agents.verification import verify_payment_link_paid
from app.core.locks import run_with_singleton_lock
from app.database.models import InterventionRecord, Opportunity, Payment
from app.database.session import session_factory
from app.events.bus import bus
from app.events.processor import process_payment_event
from app.simulation.incidents import detector_cycle

logger = logging.getLogger("simulation")

TICK_SECONDS = 1.0

# Opportunities that were treated (a link was "sent") versus deliberately left
# alone.  Recovery is delivered differently for each so the experiment can
# attribute the difference.
_TREATED_STATUSES = ("intervention_pending",)
_UNTREATED_STATUSES = ("control_holdout", "shadow_observation", "closed_not_viable")


async def _deliver_recoveries(rng: random.Random, config: SimulationConfig) -> int:
    """Let a realistic fraction of simulated customers actually pay.

    Without this the demo can never show recovered revenue: a failed simulated
    payment is generated exactly once and never succeeds afterwards, so both
    `recovered_intervention` and `recovered_natural` stay permanently at zero.
    """
    if config.recovery_rate_treatment <= 0 and config.recovery_rate_control <= 0:
        return 0

    recovered = 0

    # --- treated: the simulated payment link gets paid -> recovered_intervention
    if config.recovery_rate_treatment > 0:
        async with session_factory() as session:
            treated = (
                await session.execute(
                    select(Opportunity.id, Opportunity.amount_minor, InterventionRecord.razorpay_reference)
                    .join(InterventionRecord, InterventionRecord.opportunity_id == Opportunity.id)
                    .where(
                        Opportunity.is_synthetic.is_(True),
                        Opportunity.status.in_(_TREATED_STATUSES),
                        InterventionRecord.status == "executed",
                        InterventionRecord.razorpay_reference.is_not(None),
                    )
                    .limit(200)
                )
            ).all()
        for _opp_id, amount_minor, link_id in treated:
            if rng.random() >= config.recovery_rate_treatment:
                continue
            event = await verify_payment_link_paid(
                str(link_id),
                {
                    "id": f"pay_simrec_{uuid_lib.uuid4().hex[:12]}",
                    "amount": int(amount_minor),
                    "currency": "INR",
                    "status": "captured",
                },
            )
            if event is not None:
                recovered += 1

    # --- untreated: the customer retries on their own -> recovered_natural
    if config.recovery_rate_control > 0:
        async with session_factory() as session:
            untreated = (
                await session.execute(
                    select(Payment.razorpay_payment_id, Payment.amount_minor, Payment.method, Payment.bank)
                    .join(Opportunity, Opportunity.payment_id == Payment.id)
                    .where(
                        Opportunity.is_synthetic.is_(True),
                        Opportunity.status.in_(_UNTREATED_STATUSES),
                    )
                    .limit(200)
                )
            ).all()
        for razorpay_payment_id, amount_minor, method, bank in untreated:
            if rng.random() >= config.recovery_rate_control:
                continue
            emitted = await process_payment_event(
                "payment.captured",
                {
                    "id": razorpay_payment_id,
                    "entity": "payment",
                    "amount": int(amount_minor),
                    "currency": "INR",
                    "status": "captured",
                    "method": method,
                    "bank": bank,
                    "created_at": int(datetime.now(timezone.utc).timestamp()),
                    "_source": "simulation",
                    "_synthetic": True,
                },
            )
            for event in emitted:
                await bus.publish(event)
            recovered += 1

    if recovered:
        logger.info("simulated recovery pass resolved %d opportunities", recovered)
    return recovered


@dataclass
class SimulationConfig:
    method: str = "upi"
    bank: str = "HDFC"
    failure_rate: float = 0.65
    payments_per_minute: int = 120
    amount_min_minor: int = 100_000
    amount_max_minor: int = 3_500_000
    duration_seconds: int = 300
    seed: int | None = None
    subscription_share: float = 0.0
    label: str = "simulated incident"
    # Fraction of simulated customers who eventually pay.  The treatment rate is
    # deliberately higher than the control rate: that gap is the recovery lift
    # the control plane exists to measure.  Both default to 0.0 so an explicit
    # opt-in is required before the demo shows any recovered revenue.
    recovery_rate_treatment: float = 0.0
    recovery_rate_control: float = 0.0

    def sanitized(self) -> "SimulationConfig":
        cfg = SimulationConfig(**self.__dict__)
        cfg.failure_rate = min(0.95, max(0.0, self.failure_rate))
        cfg.payments_per_minute = max(10, min(600, self.payments_per_minute))
        cfg.duration_seconds = max(30, min(900, self.duration_seconds))
        cfg.subscription_share = min(0.8, max(0.0, self.subscription_share))
        cfg.recovery_rate_treatment = min(0.95, max(0.0, self.recovery_rate_treatment))
        cfg.recovery_rate_control = min(0.95, max(0.0, self.recovery_rate_control))
        if cfg.amount_min_minor > cfg.amount_max_minor:
            raise ValueError("amount_min_minor must be less than or equal to amount_max_minor")
        return cfg


@dataclass
class SimulationState:
    run_id: str
    config: SimulationConfig
    status: str = "running"
    started_at: float = field(default_factory=time.time)
    generated: int = 0
    failures: int = 0
    recovered: int = 0
    task: asyncio.Task | None = None
    error: str | None = None


_state: SimulationState | None = None


def simulation_status() -> dict[str, Any]:
    if _state is None:
        return {"active": False}
    elapsed = time.time() - _state.started_at
    return {
        "active": _state.status == "running",
        "run_id": _state.run_id,
        "status": _state.status,
        "label": f"SYNTHETIC — {_state.config.label}",
        "config": {
            "method": _state.config.method,
            "bank": _state.config.bank,
            "failure_rate": _state.config.failure_rate,
            "payments_per_minute": _state.config.payments_per_minute,
            "duration_seconds": _state.config.duration_seconds,
        },
        "elapsed_seconds": round(elapsed, 1),
        "generated_payments": _state.generated,
        "generated_failures": _state.failures,
        "simulated_recoveries": _state.recovered,
        "synthetic": True,
        "error": _state.error,
    }


async def _run_simulation(state: SimulationState) -> None:
    rng = random.Random(state.config.seed)
    payments_per_tick = state.config.payments_per_minute * TICK_SECONDS / 60.0
    payment_credit = 0.0
    total_ticks = int(state.config.duration_seconds / TICK_SECONDS)
    customer_seq = 0

    logger.warning(
        "SIMULATION START %s: %s/%s @ %.0f%% failures, %d/min (SYNTHETIC VOLUME)",
        state.run_id, state.config.bank, state.config.method,
        state.config.failure_rate * 100, state.config.payments_per_minute,
    )

    try:
        for tick in range(total_ticks):
            if state.status != "running":
                break
            payment_credit += payments_per_tick
            per_tick = int(payment_credit)
            payment_credit -= per_tick
            for _ in range(per_tick):
                customer_seq += 1
                state.generated += 1
                failed = rng.random() < state.config.failure_rate
                is_sub = rng.random() < state.config.subscription_share
                amount = rng.randint(
                    state.config.amount_min_minor // 100 * 100,
                    state.config.amount_max_minor // 100 * 100,
                )
                now_ts = int(datetime.now(timezone.utc).timestamp())
                entity: dict[str, Any] = {
                    "id": f"pay_sim_{state.run_id[:6]}_{customer_seq:05d}",
                    "entity": "payment",
                    "amount": amount,
                    "currency": "INR",
                    "method": state.config.method,
                    "bank": state.config.bank,
                    "email": f"sim.customer.{rng.randint(1, 400)}@simulation.local",
                    "contact": "+919800000000",
                    "invoice_id": f"inv_SIM{customer_seq:05d}" if is_sub else None,
                    "created_at": now_ts,
                    "_source": "simulation",
                    "_synthetic": True,
                    "_simulation_run_id": state.run_id,
                }
                if failed:
                    state.failures += 1
                    entity.update({
                        "status": "failed",
                        "error_code": "GATEWAY_ERROR",
                        "error_description": "Simulated degradation",
                        "error_source": "network",
                        "error_step": "payment_authorization",
                        "error_reason": "timeout",
                    })
                    emitted = await process_payment_event("payment.failed", entity)
                else:
                    entity["status"] = "captured"
                    emitted = await process_payment_event("payment.captured", entity)
                for event in emitted:
                    await bus.publish(event)
            if tick % 20 == 19:
                try:
                    await run_with_singleton_lock("recovery-incident-detector", detector_cycle)
                except Exception:
                    logger.exception("detector cycle failed during simulation")
            # Give the pipeline a few seconds to decide before paying anything
            # back, then let customers respond while traffic is still flowing.
            if tick % 10 == 9 and tick > 10:
                try:
                    state.recovered += await _deliver_recoveries(rng, state.config)
                except Exception:
                    logger.exception("recovery delivery failed during simulation")
            await asyncio.sleep(TICK_SECONDS)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        state.status = "failed"
        state.error = f"{type(exc).__name__}: {str(exc)[:500]}"
        logger.exception("simulation %s failed", state.run_id)
    finally:
        if state.status != "failed":
            state.status = "stopped"
        try:
            await run_with_singleton_lock("recovery-incident-detector", detector_cycle)
        except Exception:
            logger.exception("final detector cycle failed")
        # Final settlement pass: opportunities decided in the last few ticks
        # never got a chance to be paid back inside the loop.
        try:
            state.recovered += await _deliver_recoveries(rng, state.config)
        except Exception:
            logger.exception("final recovery delivery failed")
        logger.warning(
            "SIMULATION END %s: generated=%d failures=%d recovered=%d", state.run_id,
            state.generated, state.failures, state.recovered,
        )


async def start_simulation(config: SimulationConfig) -> dict[str, Any]:
    global _state
    if _state is not None and _state.status == "running":
        return {"started": False, "reason": "simulation already running", "status": simulation_status()}

    try:
        cfg = config.sanitized()
    except ValueError as exc:
        return {"started": False, "reason": str(exc), "status": simulation_status()}
    state = SimulationState(run_id=uuid_lib.uuid4().hex[:8], config=cfg)
    state.task = asyncio.create_task(_run_simulation(state))
    _state = state
    await asyncio.sleep(0)
    return {"started": True, "run_id": state.run_id, "status": simulation_status()}


async def stop_simulation() -> dict[str, Any]:
    global _state
    if _state is None or _state.status != "running":
        return {"stopped": False, "reason": "no active simulation"}
    _state.status = "stopping"
    if _state.task:
        _state.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _state.task
    return {"stopped": True, "final": simulation_status()}


async def shutdown_simulation() -> None:
    """Stop the in-process demo task cleanly during application shutdown."""
    if _state is not None and _state.status == "running":
        await stop_simulation()
