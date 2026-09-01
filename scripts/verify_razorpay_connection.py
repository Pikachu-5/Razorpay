import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.config import get_settings
from app.integrations.razorpay.client import RazorpayClient
from app.integrations.razorpay.errors import RazorpayError


async def main() -> int:
    settings = get_settings()
    if not settings.razorpay_configured:
        print("FAIL: RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET missing in .env")
        return 2

    client = RazorpayClient(
        key_id=settings.razorpay_key_id,
        key_secret=settings.razorpay_key_secret,
        base_url=settings.razorpay_base_url,
    )
    try:
        payments = await client.fetch_payments(count=3)
        print(f"OK: authenticated against {settings.razorpay_base_url} (test mode)")
        print(f"Fetched {len(payments)} recent payment(s):")
        for payment in payments:
            print(f"  {payment.get('id')}  status={payment.get('status')}  amount={payment.get('amount')}")
        if not payments:
            print("  (none yet — expected on a fresh test account)")
        return 0
    except RazorpayError as exc:
        print(f"FAIL: {exc}")
        return 1
    finally:
        await client.aclose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
