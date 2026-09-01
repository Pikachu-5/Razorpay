import asyncio
import os
import shutil
import tempfile
from pathlib import Path

import asyncpg

TEST_WEBHOOK_SECRET = "test_whsec_123"

# Promotion tests write a PROMOTED.json and expect it to take effect. Pointed at
# the real directory they rewrite a checked-in file, so every test run dirtied
# the working tree and could leave a different model promoted than the one the
# repository ships. Run them against a disposable copy instead.
_REPO_ARTIFACTS = Path(__file__).resolve().parents[2] / "models" / "artifacts"
_TEST_ARTIFACTS = Path(tempfile.mkdtemp(prefix="recovery-artifacts-"))
if _REPO_ARTIFACTS.exists():
    shutil.copytree(_REPO_ARTIFACTS, _TEST_ARTIFACTS, dirs_exist_ok=True)
os.environ["MODEL_ARTIFACTS_DIR"] = str(_TEST_ARTIFACTS)
TEST_DATABASE_URL = "postgresql+asyncpg://recovery:recovery@localhost:55432/recovery_test"
TEST_ADMIN_DATABASE_URL = "postgresql://recovery:recovery@localhost:55432/recovery"

os.environ["APP_ENV"] = "test"
os.environ["RAZORPAY_WEBHOOK_SECRET"] = TEST_WEBHOOK_SECRET
os.environ["RAZORPAY_KEY_ID"] = ""
os.environ["RAZORPAY_KEY_SECRET"] = ""
os.environ["RAZORPAY_MODE"] = "test"
os.environ["SHADOW_MODE"] = "false"
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

import hashlib
import hmac
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.database.session import Base, engine
from app.main import app


def sign_payload(raw: bytes, secret: str = TEST_WEBHOOK_SECRET) -> str:
    return hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()


def encode_body(body: dict[str, Any]) -> bytes:
    return json_dumps(body).encode("utf-8")


def json_dumps(body: dict[str, Any]) -> str:
    import json

    return json.dumps(body)


def payment_failed_body(
    payment_id: str = "pay_TESTFAIL001",
    amount_minor: int = 420000,
    bank: str = "HDFC",
    method: str = "netbanking",
) -> dict[str, Any]:
    return {
        "entity": "event",
        "account_id": "acc_TEST",
        "event": "payment.failed",
        "contains": ["payment"],
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "entity": "payment",
                    "amount": amount_minor,
                    "currency": "INR",
                    "status": "failed",
                    "method": method,
                    "bank": bank,
                    "error_code": "BAD_REQUEST_ERROR",
                    "error_description": "Payment failed",
                    "error_source": "bank",
                    "error_step": "payment_authorization",
                    "error_reason": "payment_failed",
                    "created_at": 1724567890,
                }
            }
        },
        "created_at": 1724567900,
    }


async def _create_schema() -> None:
    # docker-compose creates `recovery`; provision the isolated test database
    # on first run so a fresh local stack can execute pytest without setup.
    admin = await asyncpg.connect(TEST_ADMIN_DATABASE_URL)
    try:
        exists = await admin.fetchval("SELECT 1 FROM pg_database WHERE datname = 'recovery_test'")
        if not exists:
            await admin.execute("CREATE DATABASE recovery_test")
    finally:
        await admin.close()

    eng = create_async_engine(TEST_DATABASE_URL)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await eng.dispose()


async def _drop_schema() -> None:
    eng = create_async_engine(TEST_DATABASE_URL)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest.fixture(scope="session", autouse=True)
def prepare_database():
    asyncio.run(_create_schema())
    yield
    asyncio.run(_drop_schema())
    shutil.rmtree(_TEST_ARTIFACTS, ignore_errors=True)


@pytest.fixture(autouse=True)
def guard_repo_artifacts():
    """Fail loudly if a test ever writes into the checked-in artifacts directory."""
    pointer = _REPO_ARTIFACTS / "PROMOTED.json"
    before = pointer.read_bytes() if pointer.exists() else None
    yield
    after = pointer.read_bytes() if pointer.exists() else None
    assert before == after, "a test modified the checked-in models/artifacts/PROMOTED.json"


@pytest_asyncio.fixture(autouse=True)
async def clean_tables():
    yield
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE TABLE durable_events, raw_events, payment_link_states, "
                "revenue_adjustments, invoice_states, subscription_states, payment_downtimes, razorpay_orders, "
                "interventions, decision_audit, opportunities, payments, customers, incidents, detector_states "
                "RESTART IDENTITY CASCADE"
            )
        )
    await engine.dispose()


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
