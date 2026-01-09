# =======================================================================
# services/airtime_providers/service.py
# =======================================================================
from __future__ import annotations

from services.airtime_providers.types import AirtimeResult
from services.airtime_providers.clubkonnect import buy_airtime


def _pick(d: dict, *keys, default=None):
    """Return the first non-empty value among keys."""
    for k in keys:
        v = d.get(k)
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        return v
    return default


def _norm_str(v) -> str:
    return str(v or "").strip()


async def send_airtime(phone: str, amount: int) -> AirtimeResult:
    """
    Provider-agnostic entry point (currently ClubKonnect only).

    Interprets ClubKonnect/Nellobytes responses based on:
    - status (e.g. ORDER_RECEIVED, ORDER_COMPLETED, MINIMUM_50, INVALID_RECIPIENT)
    - statuscode (e.g. 100, 200)
    - orderid/orderId for reference
    """

    data = await buy_airtime(phone=phone, amount=amount)

    # ------------------------------------------------------------
    # SPECIAL CASE: we need the user to choose a network
    # (This is returned by our own buy_airtime() when guess_network fails)
    # ------------------------------------------------------------
    if _norm_str(data.get("status")).lower() == "need_network":
        return AirtimeResult(
            success=False,  # not a provider failure; it needs user action
            provider="clubkonnect",
            reference="",
            message=_norm_str(data.get("message")) or "Please choose your network.",
            raw=data,
        )

    status_raw = _norm_str(_pick(data, "status", "Status", default=""))
    status = status_raw.upper()

    statuscode_raw = _pick(data, "statuscode", "statusCode", "StatusCode", "STATUSCODE", default="")
    statuscode = _norm_str(statuscode_raw)

    # order reference (from screenshot: "orderid")
    order_id = _pick(
        data,
        "orderid", "orderId", "OrderID", "OrderId", "ORDERID",
        "order_id", "orderID",
        default="",
    )
    reference = _norm_str(order_id)

    # Better message selection (provider sometimes returns "message", sometimes only "status")
    message = _pick(
        data,
        "message", "Message",
        "description", "Description",
        "remark", "Remark",
        "ordernote", "orderNote", "OrderNote",
        default=None,
    )
    message = _norm_str(message) or status_raw or "Airtime response received."

    # ------------------------------------------------------------
    # SUCCESS / COMPLETED
    # ------------------------------------------------------------
    if statuscode == "200" or status in ("ORDER_COMPLETED", "COMPLETED", "SUCCESS"):
        return AirtimeResult(
            success=True,
            provider="clubkonnect",
            reference=reference,
            message=message,
            raw=data,
        )

    # ------------------------------------------------------------
    # ACCEPTED / PROCESSING (do NOT mark as failed)
    # From docs/screenshots: ORDER_RECEIVED means accepted
    # ------------------------------------------------------------
    if statuscode in ("100", "300") or status in ("ORDER_RECEIVED", "ORDER_PROCESSED", "ORDER_PROCESSING"):
        return AirtimeResult(
            success=True,
            provider="clubkonnect",
            reference=reference,
            message=message or "ORDER_RECEIVED",
            raw=data,
        )

    # ------------------------------------------------------------
    # PENDING / ON-HOLD (treat as success=True so notifier doesn't stop;
    # but you may later reconcile)
    # ------------------------------------------------------------
    if statuscode in ("201", "600", "601") or status in ("PENDING", "ON_HOLD", "ON-HOLD", "PROCESSING"):
        return AirtimeResult(
            success=True,
            provider="clubkonnect",
            reference=reference,
            message=message or "Processing, please check back.",
            raw=data,
        )

    # ------------------------------------------------------------
    # HARD FAILURE (includes MINIMUM_50, INVALID_RECIPIENT, etc.)
    # ------------------------------------------------------------
    return AirtimeResult(
        success=False,
        provider="clubkonnect",
        reference=reference,
        message=message or "Airtime request failed",
        raw=data,
    )
