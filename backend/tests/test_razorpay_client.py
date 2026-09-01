import httpx
import pytest

from app.integrations.razorpay.client import RazorpayClient
from app.integrations.razorpay.errors import (
    RazorpayAuthError,
    RazorpayRequestError,
    RazorpayServerError,
)

pytestmark = pytest.mark.asyncio


def make_client(handler, max_attempts: int = 3):
    return RazorpayClient(
        key_id="test_key",
        key_secret="test_secret",
        base_url="https://mock.razorpay.test",
        transport=httpx.MockTransport(handler),
        backoff_base=0,
        max_attempts=max_attempts,
    )


async def test_success_returns_json():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        assert request.url.path == "/v1/payments/pay_123"
        return httpx.Response(200, json={"id": "pay_123", "status": "captured"})

    client = make_client(handler)
    result = await client.fetch_payment("pay_123")
    await client.aclose()
    assert result["id"] == "pay_123"
    assert calls["n"] == 1


async def test_retries_on_500_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(500, json={"error": {"code": "SERVER_ERROR", "description": "oops"}})
        return httpx.Response(200, json={"id": "plink_ok"})

    client = make_client(handler)
    result = await client.request("GET", "/payments/pay_x")
    await client.aclose()
    assert result == {"id": "plink_ok"}
    assert calls["n"] == 3


async def test_retries_exhausted_raises_server_error():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, json={"error": {"code": "SERVER_ERROR", "description": "down"}})

    client = make_client(handler)
    with pytest.raises(RazorpayServerError):
        await client.fetch_payment("pay_x")
    await client.aclose()
    assert calls["n"] == 3


async def test_auth_error_no_retry():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, json={"error": {"code": "BAD_REQUEST_ERROR", "description": "invalid key"}})

    client = make_client(handler)
    with pytest.raises(RazorpayAuthError):
        await client.fetch_payments()
    await client.aclose()
    assert calls["n"] == 1


async def test_client_error_4xx_no_retry():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, json={"error": {"code": "BAD_REQUEST_ERROR", "description": "bad input"}})

    client = make_client(handler)
    with pytest.raises(RazorpayRequestError):
        await client.fetch_payment("pay_missing")
    await client.aclose()
    assert calls["n"] == 1


async def test_rate_limit_retries_and_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                429,
                headers={"retry-after": "0"},
                json={"error": {"code": "RATE_LIMIT_EXCEEDED", "description": "slow down"}},
            )
        return httpx.Response(200, json={"items": [], "count": 0})

    client = make_client(handler)
    result = await client.fetch_payments()
    await client.aclose()
    assert result == []
    assert calls["n"] == 2


async def test_network_error_retries_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, json={"id": "pay_after_retry"})

    client = make_client(handler)
    result = await client.fetch_payment("pay_x")
    await client.aclose()
    assert result["id"] == "pay_after_retry"


async def test_create_payment_link_request_shape():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"id": "plink_TEST001", "short_url": "https://rzp.io/i/test", "status": "created"},
        )

    client = make_client(handler)
    result = await client.create_payment_link(
        amount_minor=420000,
        reference_id="opp_123",
        customer_name="Test Customer",
        customer_contact="+919999999999",
        description="Recovery payment for failed order",
    )
    await client.aclose()

    assert result["id"] == "plink_TEST001"
    assert captured["path"] == "/v1/payment_links"
    body = captured["body"]
    assert body["amount"] == 420000
    assert body["currency"] == "INR"
    assert "type" not in body
    assert "view_less" not in body
    assert body["reference_id"] == "opp_123"
    assert body["customer"]["contact"] == "+919999999999"


async def test_fetch_payments_parses_items():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": [{"id": "pay_1"}, {"id": "pay_2"}], "count": 2})

    client = make_client(handler)
    items = await client.fetch_payments()
    await client.aclose()
    assert [item["id"] for item in items] == ["pay_1", "pay_2"]


async def test_order_and_order_payments_endpoints():
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/payments"):
            return httpx.Response(200, json={"items": [{"id": "pay_retry", "status": "failed"}]})
        return httpx.Response(200, json={"id": "order_1", "status": "attempted"})

    client = make_client(handler)
    assert (await client.fetch_order("order_1"))["status"] == "attempted"
    assert (await client.fetch_order_payments("order_1"))[0]["id"] == "pay_retry"
    await client.aclose()
    assert paths == ["/v1/orders/order_1", "/v1/orders/order_1/payments"]


async def test_downtime_and_subscription_reconciliation_endpoints():
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/v1/payments/downtimes":
            return httpx.Response(200, json={"items": [{"id": "down_1"}]})
        if request.url.path == "/v1/invoices":
            assert request.url.params["subscription_id"] == "sub_1"
            return httpx.Response(200, json={"items": [{"id": "inv_1"}]})
        return httpx.Response(200, json={"id": "sub_1", "status": "pending"})

    client = make_client(handler)
    assert (await client.fetch_downtimes())[0]["id"] == "down_1"
    assert (await client.fetch_subscription("sub_1"))["status"] == "pending"
    assert (await client.fetch_subscription_invoices("sub_1"))[0]["id"] == "inv_1"
    await client.aclose()
    assert paths == ["/v1/payments/downtimes", "/v1/subscriptions/sub_1", "/v1/invoices"]


async def test_payment_link_supports_expiry_reminders_and_method_options():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"id": "plink_1", "status": "created"})

    client = make_client(handler)
    await client.create_payment_link(
        amount_minor=10000, reference_id="opp_1", expire_by=1893456000,
        reminder_enable=True,
        options={"checkout": {"method": {"upi": False, "card": True}}},
    )
    await client.aclose()
    assert captured["expire_by"] == 1893456000
    assert captured["reminder_enable"] is True
    assert captured["options"]["checkout"]["method"]["upi"] is False
