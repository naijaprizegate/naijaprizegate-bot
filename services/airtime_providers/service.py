# =======================================================================
# services/airtime_providers/service.py
# ======================================================================
from __future__ import annotations

from services.airtime_providers.types import AirtimeResult
from services.airtime_providers.clubkonnect import buy_airtime


def _pick(d: dict, *keys, default=None):
    """Return the first non-empty value among keys (supports mixed provider key styles)."""
    for k in keys:
        v = d.get(k)
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        return v
    return default


async def send_airtime(phone: str, amount: int) -> AirtimeResult:
    """
    Provider-agnostic entry point.
    Currently uses ClubKonnect/Nellobytes only.

    IMPORTANT NOTES (based on how ClubKonnect behaves):
    - Some responses mean "accepted/processing" (not an instant failure).
    - Keys may be camelCase (statusCode, orderId, description) or lowercase (statuscode, orderid, message).
    - Treat 'ORDER_RECEIVED' / 'ORDER_PROCESSED' and statusCode 100/300 as ACCEPTED.
    - Treat statusCode 201 (network unresponsive) and 600/601 (on-hold) as PENDING (not failed).
    - Treat statusCode 200 / ORDER_COMPLETED as SUCCESS.
    """

    data = await buy_airtime(phone=phone, amount=amount)

    # Normalize status + codes across possible response formats
    status_raw = str(_pick(data, "status", "Status", default=""))
    status = status_raw.strip().upper()

    statuscode_raw = _pick(data, "statusCode", "statuscode", "StatusCode", "STATUSCODE", default="")
    statuscode = str(statuscode_raw).strip()

    order_id = _pick(data, "orderId", "orderid", "OrderID", "order_id", "orderID", default="")
    reference = str(order_id or "")

    # Prefer provider explanations
    message = (
        _pick(data, "description", "Description", "remark", "Remark", "message", "Message", default=None)
        or status_raw
        or "Airtime request processed."
    )

    # ---- SUCCESS (completed) ----
    if statuscode == "200" or status in ("ORDER_COMPLETED", "COMPLETED", "SUCCESS"):
        return AirtimeResult(
            success=True,
            provider="clubkonnect",
            reference=reference,
            message=message or "Transaction was successful.",
            raw=data,
        )

    # ---- ACCEPTED / PROCESSING (treat as success on your side, but payout may still be in progress) ----
    # 100 = order received, 300 = order processed
    if statuscode in ("100", "300") or status in ("ORDER_RECEIVED", "ORDER_PROCESSED"):
        return AirtimeResult(
            success=True,
            provider="clubkonnect",
            reference=reference,
            message=message or "ORDER_RECEIVED",
            raw=data,
        )

    # ---- PENDING / ON-HOLD (NOT a hard failure; you should retry / reconcile later) ----
    # 201 = network unresponsive, 600/601 = on-hold variants (provider may later complete)
    if statuscode in ("201", "600", "601"):
        return AirtimeResult(
            success=True,  # IMPORTANT: don't mark as failed; treat as pending processing
            provider="clubkonnect",
            reference=reference,
            message=message or "Processing, please check back.",
            raw=data,
        )

    # ---- HARD FAILURE ----
    # Examples: MINIMUM_100, INVALID_RECIPIENT, INVALID_MOBILENETWORK, INSUFFICIENT_BALANCE, etc.
    return AirtimeResult(
        success=False,
        provider="clubkonnect",
        reference=reference,
        message=message or "Airtime request failed",
        raw=data,
    )
