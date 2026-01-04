# =======================================================================
# services/airtime_providers/service.py
# ======================================================================
from __future__ import annotations

from services.airtime_providers.types import AirtimeResult
from services.airtime_providers.clubkonnect import buy_airtime


async def send_airtime(phone: str, amount: int) -> AirtimeResult:
    """
    Provider-agnostic entry point.
    Right now it uses Clubkonnect only.
    Later we can add VTU / Flutterwave fallback.
    """
    data = await buy_airtime(phone=phone, amount=amount)

    # Clubkonnect returns statuscode like "100" and status like "ORDER_RECEIVED"
    status = (data.get("status") or "").upper()
    statuscode = str(data.get("statuscode") or "")

    # Treat ORDER_RECEIVED as success because it means accepted for processing
    if status in ("ORDER_RECEIVED", "ORDER_COMPLETED") or statuscode in ("100", "200"):
        return AirtimeResult(
            success=True,
            provider="clubkonnect",
            reference=str(data.get("orderid") or data.get("order_id") or ""),
            message=data.get("status") or "ORDER_RECEIVED",
            raw=data,
        )

    return AirtimeResult(
        success=False,
        provider="clubkonnect",
        message=data.get("message") or data.get("status") or "Airtime request failed",
        raw=data,
    )
