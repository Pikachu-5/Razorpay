import asyncio
import random
from typing import Any

import httpx

from app.integrations.razorpay.errors import (
    RazorpayAuthError,
    RazorpayRateLimitError,
    RazorpayRequestError,
    RazorpayServerError,
)

DEFAULT_BASE_URL = "https://api.razorpay.com"


def _parse_retry_after(headers: httpx.Headers) -> float | None:
    raw = headers.get("retry-after")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


class RazorpayClient:
    def __init__(
        self,
        *,
        key_id: str,
        key_secret: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 10.0,
        max_attempts: int = 3,
        backoff_base: float = 0.4,
        backoff_cap: float = 8.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._max_attempts = max(1, max_attempts)
        self._backoff_base = backoff_base
        self._backoff_cap = backoff_cap
        self._http = httpx.AsyncClient(
            base_url=f"{base_url.rstrip('/')}/v1",
            auth=(key_id, key_secret),
            timeout=timeout,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _sleep_backoff(self, attempt: int, retry_after: float | None = None) -> None:
        if retry_after is not None:
            await asyncio.sleep(retry_after)
            return
        delay = min(self._backoff_cap, self._backoff_base * (2 ** (attempt - 1)))
        delay += random.uniform(0, self._backoff_base / 2)
        await asyncio.sleep(delay)

    async def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await self._http.request(method, path, json=json_body, params=params)
            except httpx.TransportError as exc:
                last_error = RazorpayServerError(None, f"network error: {exc}")
                if attempt == self._max_attempts:
                    raise last_error from exc
                await self._sleep_backoff(attempt)
                continue

            status = response.status_code
            if status < 400:
                if not response.content:
                    return {}
                return response.json()

            body: dict[str, Any]
            try:
                body = response.json()
            except ValueError:
                body = {}
            error = body.get("error") if isinstance(body, dict) else None
            code = str(error.get("code", "UNKNOWN")) if isinstance(error, dict) else "UNKNOWN"
            description = str(error.get("description", response.text[:200])) if isinstance(error, dict) else response.text[:200]

            if status == 401:
                raise RazorpayAuthError(description)
            if status == 429 or status >= 500:
                retry_after = _parse_retry_after(response.headers)
                last_error = (
                    RazorpayRateLimitError(status, code, description, retry_after)
                    if status == 429
                    else RazorpayServerError(status, description)
                )
                if attempt == self._max_attempts:
                    raise last_error
                await self._sleep_backoff(attempt, retry_after)
                continue
            raise RazorpayRequestError(status, code, description)

        raise last_error if last_error else RazorpayServerError(None, "unreachable")

    async def fetch_payments(self, *, count: int = 10) -> list[dict[str, Any]]:
        data = await self.request("GET", "/payments", params={"count": count})
        items = data.get("items", []) if isinstance(data, dict) else []
        return items

    async def fetch_payment(self, payment_id: str) -> dict[str, Any]:
        return await self.request("GET", f"/payments/{payment_id}")

    async def create_payment_link(
        self,
        *,
        amount_minor: int,
        reference_id: str,
        customer_name: str | None = None,
        customer_email: str | None = None,
        customer_contact: str | None = None,
        description: str | None = None,
        currency: str = "INR",
        notes: dict[str, str] | None = None,
        expire_by: int | None = None,
        reminder_enable: bool = False,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "amount": amount_minor,
            "currency": currency,
            "reference_id": reference_id,
        }
        customer: dict[str, Any] = {}
        if customer_name:
            customer["name"] = customer_name
        if customer_email:
            customer["email"] = customer_email
        if customer_contact:
            customer["contact"] = customer_contact
        if customer:
            body["customer"] = customer
        if description:
            body["description"] = description
        if notes:
            body["notes"] = notes
        if expire_by:
            body["expire_by"] = expire_by
        body["reminder_enable"] = reminder_enable
        if options:
            body["options"] = options
        return await self.request("POST", "/payment_links", json_body=body)

    async def fetch_payment_link(self, link_id: str) -> dict[str, Any]:
        return await self.request("GET", f"/payment_links/{link_id}")

    async def notify_payment_link(self, link_id: str, medium: str = "sms") -> dict[str, Any]:
        return await self.request("POST", f"/payment_links/{link_id}/notify_by/{medium}", json_body={})

    async def cancel_payment_link(self, link_id: str) -> dict[str, Any]:
        return await self.request("POST", f"/payment_links/{link_id}/cancel")

    async def update_payment_link(self, link_id: str, **changes: Any) -> dict[str, Any]:
        allowed = {"accept_partial", "reference_id", "expire_by", "notes", "reminder_enable"}
        return await self.request(
            "PATCH", f"/payment_links/{link_id}",
            json_body={key: value for key, value in changes.items() if key in allowed},
        )

    async def fetch_order(self, order_id: str) -> dict[str, Any]:
        return await self.request("GET", f"/orders/{order_id}")

    async def fetch_order_payments(self, order_id: str) -> list[dict[str, Any]]:
        data = await self.request("GET", f"/orders/{order_id}/payments")
        return list(data.get("items", [])) if isinstance(data, dict) else []

    async def fetch_downtimes(self) -> list[dict[str, Any]]:
        data = await self.request("GET", "/payments/downtimes")
        return list(data.get("items", [])) if isinstance(data, dict) else []

    async def fetch_downtime(self, downtime_id: str) -> dict[str, Any]:
        return await self.request("GET", f"/payments/downtimes/{downtime_id}")

    async def fetch_subscription(self, subscription_id: str) -> dict[str, Any]:
        return await self.request("GET", f"/subscriptions/{subscription_id}")

    async def fetch_subscription_invoices(self, subscription_id: str) -> list[dict[str, Any]]:
        data = await self.request("GET", "/invoices", params={"subscription_id": subscription_id})
        return list(data.get("items", [])) if isinstance(data, dict) else []

    async def fetch_methods(self) -> dict[str, Any]:
        return await self.request("GET", "/methods")


_client: RazorpayClient | None = None


async def get_client() -> RazorpayClient:
    global _client
    if _client is None:
        from app.core.config import get_settings

        settings = get_settings()
        if not settings.razorpay_configured:
            raise RazorpayAuthError("RAZORPAY_KEY_ID/SECRET not configured")
        _client = RazorpayClient(
            key_id=settings.razorpay_key_id,
            key_secret=settings.razorpay_key_secret,
            base_url=settings.razorpay_base_url,
        )
    return _client


async def reset_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
