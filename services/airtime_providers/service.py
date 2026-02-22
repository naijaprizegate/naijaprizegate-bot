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


def _is_retryable_http(data: dict) -> bool:
    """
    Mark provider/network/server errors as retryable.
    We rely on clubkonnect.py including:
      - http_status
      - message like "Non-JSON response from provider"
    """
    try:
        http_status = int(data.get("http_status") or 0)
    except Exception:
        http_status = 0

    msg = _norm_str(data.get("message")).lower()

    # Retryable if provider/server is unavailable or returned invalid response
    if http_status >= 500:
        return True
    if "non-json response" in msg:
        return True
    if "timeout" in msg or "timed out" in msg:
        return True
    if "provider request failed" in msg:
        return True

    return False


async def send_airtime(phone: str, amount: int) -> AirtimeResult:
    """
    Provider-agnostic entry point (currently ClubKonnect only).

    Returns AirtimeResult(success=...) plus extra hints in raw:
      raw["retryable"] = True/False
      raw["provider_state"] = "completed"|"accepted"|"pending"|"need_network"|"failed"
      raw["failure_reason"] = e.g. "INSUFFICIENT_BALANCE"
    """
    data = await buy_airtime(phone=phone, amount=amount)
    if not isinstance(data, dict):
        data = {"status": "error", "message": "Unexpected provider response type", "raw": str(data)[:300]}

    # ------------------------------------------------------------
    # SPECIAL CASE: we need the user to choose a network
    # (Returned by our own buy_airtime() when guess_network fails)
    # ------------------------------------------------------------
    if _norm_str(data.get("status")).lower() == "need_network":
        data["retryable"] = False
        data["provider_state"] = "need_network"
        return AirtimeResult(
            success=False,  # needs user action
            provider="clubkonnect",
            reference="",
            message=_norm_str(data.get("message")) or "Please choose your network.",
            raw=data,
        )

    # Normalized fields
    status_raw = _norm_str(_pick(data, "status", "Status", default=""))
    status = status_raw.upper()

    statuscode_raw = _pick(data, "statuscode", "statusCode", "StatusCode", "STATUSCODE", default="")
    statuscode = _norm_str(statuscode_raw)

    # Provider reference
    order_id = _pick(
        data,
        "orderid", "orderId", "OrderID", "OrderId", "ORDERID",
        "order_id", "orderID",
        default="",
    )
    reference = _norm_str(order_id)

    # Best message
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
    # HARD FAILURE: INSUFFICIENT_BALANCE (NOT retryable automatically)
    # ------------------------------------------------------------
    if status in ("INSUFFICIENT_BALANCE", "LOW_BALANCE", "INSUFFICIENT FUND", "INSUFFICIENT_FUNDS"):
        data["retryable"] = False
        data["provider_state"] = "failed"
        data["failure_reason"] = "INSUFFICIENT_BALANCE"
        return AirtimeResult(
            success=False,
            provider="clubkonnect",
            reference=reference,
            message="INSUFFICIENT_BALANCE",
            raw=data,
        )

    # ------------------------------------------------------------
    # RETRYABLE FAILURES: server down / non-json / 5xx / timeouts
    # ------------------------------------------------------------
    if _is_retryable_http(data):
        data["retryable"] = True
        data["provider_state"] = "failed"
        data["failure_reason"] = "RETRYABLE_PROVIDER_ERROR"
        return AirtimeResult(
            success=False,
            provider="clubkonnect",
            reference=reference,
            message=message or "Temporary provider error. Retryable.",
            raw=data,
        )

    # ------------------------------------------------------------
    # SUCCESS / COMPLETED
    # ------------------------------------------------------------
    if statuscode == "200" or status in ("ORDER_COMPLETED", "COMPLETED", "SUCCESS"):
        data["retryable"] = False
        data["provider_state"] = "completed"
        return AirtimeResult(
            success=True,
            provider="clubkonnect",
            reference=reference,
            message=message,
            raw=data,
        )

    # ------------------------------------------------------------
    # ACCEPTED / PROCESSING
    # NOTE: Keeping your existing behavior (success=True) so you don't break flow.
    # If you later add reconciliation, you can change this to success=False + "pending".
    # ------------------------------------------------------------
    if statuscode in ("100", "300") or status in ("ORDER_RECEIVED", "ORDER_PROCESSED", "ORDER_PROCESSING"):
        data["retryable"] = False
        data["provider_state"] = "accepted"
        return AirtimeResult(
            success=True,
            provider="clubkonnect",
            reference=reference,
            message=message or "ORDER_RECEIVED",
            raw=data,
        )

    # ------------------------------------------------------------
    # PENDING / ON-HOLD (same as your current logic)
    # ------------------------------------------------------------
    if statuscode in ("201", "600", "601") or status in ("PENDING", "ON_HOLD", "ON-HOLD", "PROCESSING"):
        data["retryable"] = False
        data["provider_state"] = "pending"
        return AirtimeResult(
            success=True,
            provider="clubkonnect",
            reference=reference,
            message=message or "Processing, please check back.",
            raw=data,
        )

    # ------------------------------------------------------------
    # DEFAULT: HARD FAILURE (not retryable unless you decide otherwise)
    # ------------------------------------------------------------
    data["retryable"] = False
    data["provider_state"] = "failed"
    data["failure_reason"] = status or "UNKNOWN_FAILURE"

    return AirtimeResult(
        success=False,
        provider="clubkonnect",
        reference=reference,
        message=message or "Airtime request failed",
        raw=data,
    )
