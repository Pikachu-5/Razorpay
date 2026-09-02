import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.events import router as events_router
from app.api.health import router as health_router
from app.api.metrics import router as metrics_router
from app.api.ml import router as ml_router
from app.api.opportunities import router as opportunities_router
from app.api.policy import router as policy_router
from app.api.razorpay_state import router as razorpay_state_router
from app.api.reconciliation import router as reconciliation_router
from app.api.simulation import router as simulation_router
from app.api.stream import router as stream_router
from app.api.webhooks import router as webhooks_router
from app.core.config import get_settings
from app.core.locks import run_with_singleton_lock
from app.core.logging import configure_logging
from app.database.session import ensure_runtime_schema
from app.events.processor import start_consumer_task, start_sweeper_task, stop_consumer_task
from app.simulation.engine import shutdown_simulation
from app.simulation.incidents import detector_cycle

logger = logging.getLogger("lifespan")


def _announce_control_plane_posture(settings) -> None:
    """Say out loud, once, whether operator actions are authenticated.

    An unauthenticated control plane is a legitimate posture for a public
    demonstration and an incident in any other context. The difference is only
    ever visible if the process states which one it believes it is running.
    """
    if settings.control_plane_api_key:
        logger.info("control plane: authenticated (X-Control-Plane-Key required)")
    elif settings.is_local_env:
        logger.info("control plane: OPEN (APP_ENV=%s local development)", settings.app_env)
    elif settings.open_demo_active:
        logger.warning(
            "control plane: OPEN DEMO -- operator actions are UNAUTHENTICATED. "
            "Razorpay mode is '%s' and forced model promotion still requires an "
            "admin key. Unset CONTROL_PLANE_OPEN_DEMO for any non-demo install.",
            settings.razorpay_mode,
        )
    else:
        logger.error(
            "control plane: NO KEY CONFIGURED -- every operator action will fail "
            "closed with 503 until CONTROL_PLANE_API_KEY is set"
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _announce_control_plane_posture(get_settings())
    await ensure_runtime_schema()
    consumer = start_consumer_task()
    sweeper = start_sweeper_task()

    async def detector_loop():
        while True:
            await asyncio.sleep(60)
            try:
                await run_with_singleton_lock("recovery-incident-detector", detector_cycle)
            except Exception:
                logger.exception("detector cycle failed")

    detector = asyncio.create_task(detector_loop())
    yield
    await stop_consumer_task(consumer)
    await stop_consumer_task(sweeper)
    detector.cancel()
    with __import__("contextlib").suppress(asyncio.CancelledError):
        await detector
    await shutdown_simulation()


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()
    docs_enabled = settings.app_env.lower() in {"dev", "test", "local"}
    app = FastAPI(
        title="Revenue Recovery Control Plane",
        version="0.1.0",
        docs_url="/docs" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health_router)
    app.include_router(events_router)
    app.include_router(metrics_router)
    app.include_router(ml_router)
    app.include_router(opportunities_router)
    app.include_router(policy_router)
    app.include_router(reconciliation_router)
    app.include_router(razorpay_state_router)
    app.include_router(simulation_router)
    app.include_router(webhooks_router)
    app.include_router(stream_router)
    app.state.settings = settings
    return app



app = create_app()
