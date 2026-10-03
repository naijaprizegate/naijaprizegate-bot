# ==========================================================
# services/airtime_providers/clubkonnect.py
# ClubKonnect / Nellobytes Airtime (GET) - APIAirtimeV1.asp
# ==========================================================
from __future__ import annotations

import os
import uuid
import logging
from typing import Optional, Dict, Any

import httpx

from services.airtime_providers.base import AirtimeProvider
from services.airtime_providers.types import AirtimeResult

logger = logging.getLogger(__name__)

CK_USER_ID = os.getenv("CLUBKONNECT_USER_ID")
CK_API_KEY = os.getenv("CLUBKONNECT_API_KEY")
CK_BASE_URL = os.getenv(
    "CLUBKONNECT_BASE_URL",
    "https://www.nellobytesystems.com",
).rstrip("/")

# Optional: let you enforce your own minimum.
CK_MIN_AMOUNT = int(os.getenv("CLUBKONNECT_MIN_AMOUNT", "50"))


# Network codes from ClubKonnect docs
NETWORK_CODE = {
    "mtn": "01",
    "glo": "02",
    "9mobile": "03",
    "airtel": "04",
}


def normalize_phone(phone: str) -> str:
    """
    Normalize to Nigerian format: 11 digits starting with 0.
    Accepts +234xxxxxxxxxx as input too.
    """
    p = (phone or "").strip()

    # Convert +2348012345678 -> 08012345678
    if p.startswith("+234"):
        p = "0" + p[4:]

    # Keep digits only
    p = "".join(ch for ch in p if ch.isdigit())
    return p


def guess_network(phone: str) -> Optional[str]:
    """
    Simple network guess by prefix.
    """
    p = normalize_phone(phone)

    MTN = (
        "0703", "0704", "0706", "0803", "0806",
        "0810", "0813", "0814", "0816", "0903",
        "0906", "0913", "0916",
    )
    AIRTEL = (
        "0701", "0708", "0802", "0808", "0812",
        "0901", "0902", "0904", "0907", "0912",
    )
    GLO = (
        "0705", "0805", "0807", "0811", "0815",
        "0905", "0915",
    )
    ETISALAT = (
        "0809", "0817", "0818", "0908", "0909",
    )

    if p.startswith(MTN):
        return "mtn"
    if p.startswith(AIRTEL):
        return "airtel"
    if p.startswith(GLO):
        return "glo"
    if p.startswith(ETISALAT):
        return "9mobile"

    return None


class ClubKonnectProvider(AirtimeProvider):
    """
    ClubKonnect implementation of the common AirtimeProvider interface.
    """

    name = "clubkonnect"

    async def send_airtime(
        self,
        phone: str,
        amount: int,
        request_id: Optional[str] = None,
        network: Optional[str] = None,
    ) -> AirtimeResult:
        """
        Send airtime through ClubKonnect/Nellobytes.
        """

        p = normalize_phone(phone)

        # Basic phone validation
        if len(p) != 11 or not p.startswith("0"):
            return AirtimeResult(
                success=False,
                provider=self.name,
                message=(
                    "Invalid phone number. Use 11-digit Nigerian "
                    "format e.g. 08012345678."
                ),
            )

        try:
            amt = int(amount)
        except Exception:
            return AirtimeResult(
                success=False,
                provider=self.name,
                message="Invalid amount.",
            )

        if amt < CK_MIN_AMOUNT:
            return AirtimeResult(
                success=False,
                provider=self.name,
                message=f"Minimum airtime amount is {CK_MIN_AMOUNT}",
            )

        net = (network or guess_network(p) or "").lower()

        if not net:
            return AirtimeResult(
                success=False,
                provider=self.name,
                message="Could not detect network. Please choose your network.",
                raw={
                    "status": "need_network",
                    "phone": p,
                    "amount": amt,
                },
            )

        mobile_network_code = NETWORK_CODE.get(net)

        if not mobile_network_code:
            return AirtimeResult(
                success=False,
                provider=self.name,
                message="Unsupported/unknown network. Please choose your network.",
                raw={
                    "status": "need_network",
                    "phone": p,
                    "amount": amt,
                },
            )

        rid = request_id or f"NP-{uuid.uuid4()}"

        params = {
            "UserID": CK_USER_ID,
            "APIKey": CK_API_KEY,
            "MobileNetwork": mobile_network_code,
            "Amount": str(amt),
            "MobileNumber": p,
            "RequestID": rid,
        }

        url = f"{CK_BASE_URL}/APIAirtimeV1.asp"

        # Never log API credentials or the full URL with query parameters.
        logger.info(
            "ClubKonnect airtime request | phone=%s amount=%s "
            "network=%s request_id=%s",
            p,
            amt,
            net,
            rid,
        )

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, params=params)
        except Exception as e:
            return AirtimeResult(
                success=False,
                provider=self.name,
                reference=rid,
                message=f"Provider request failed: {e.__class__.__name__}",
                raw={
                    "status": "error",
                    "retryable": True,
                    "provider_state": "failed",
                    "failure_reason": "RETRYABLE_PROVIDER_ERROR",
                },
            )

        try:
            data = resp.json()

            if not isinstance(data, dict):
                return AirtimeResult(
                    success=False,
                    provider=self.name,
                    reference=rid,
                    message="Unexpected provider response format",
                    raw={
                        "status": "error",
                        "http_status": resp.status_code,
                        "raw": str(data)[:300],
                    },
                )

        except Exception:
            data = {
                "status": "error",
                "message": "Non-JSON response from provider",
                "http_status": resp.status_code,
                "raw": (resp.text or "")[:300],
            }

        logger.info(
            "ClubKonnect airtime response | request_id=%s status=%s "
            "statuscode=%s",
            rid,
            data.get("status"),
            data.get("statuscode") or data.get("statusCode"),
        )

        status_raw = str(data.get("status") or "").strip()
        status = status_raw.upper()

        statuscode = str(
            data.get("statuscode")
            or data.get("statusCode")
            or data.get("StatusCode")
            or ""
        ).strip()

        reference = str(
            data.get("orderid")
            or data.get("orderId")
            or data.get("OrderID")
            or data.get("OrderId")
            or data.get("ORDERID")
            or data.get("order_id")
            or data.get("orderID")
            or rid
            or ""
        ).strip()

        message = (
            data.get("message")
            or data.get("Message")
            or data.get("description")
            or data.get("Description")
            or data.get("remark")
            or data.get("Remark")
            or data.get("ordernote")
            or data.get("orderNote")
            or data.get("OrderNote")
            or status_raw
            or "Airtime response received."
        )

        message = str(message).strip()

        # Insufficient provider balance
        if status in (
            "INSUFFICIENT_BALANCE",
            "LOW_BALANCE",
            "INSUFFICIENT FUND",
            "INSUFFICIENT_FUNDS",
        ):
            data["retryable"] = False
            data["provider_state"] = "failed"
            data["failure_reason"] = "INSUFFICIENT_BALANCE"

            return AirtimeResult(
                success=False,
                provider=self.name,
                reference=reference,
                message="INSUFFICIENT_BALANCE",
                raw=data,
            )

        # Retryable provider/server errors
        try:
            http_status = int(data.get("http_status") or resp.status_code or 0)
        except Exception:
            http_status = 0

        message_lower = message.lower()

        retryable = (
            http_status >= 500
            or "non-json response" in message_lower
            or "timeout" in message_lower
            or "timed out" in message_lower
            or "provider request failed" in message_lower
        )

        if retryable:
            data["retryable"] = True
            data["provider_state"] = "failed"
            data["failure_reason"] = "RETRYABLE_PROVIDER_ERROR"

            return AirtimeResult(
                success=False,
                provider=self.name,
                reference=reference,
                message=message or "Temporary provider error. Retryable.",
                raw=data,
            )

        # Completed
        if statuscode == "200" or status in (
            "ORDER_COMPLETED",
            "COMPLETED",
            "SUCCESS",
        ):
            data["retryable"] = False
            data["provider_state"] = "completed"

            return AirtimeResult(
                success=True,
                provider=self.name,
                reference=reference,
                message=message,
                raw=data,
            )

        # Accepted / processing
        if statuscode in ("100", "300") or status in (
            "ORDER_RECEIVED",
            "ORDER_PROCESSED",
            "ORDER_PROCESSING",
        ):
            data["retryable"] = False
            data["provider_state"] = "accepted"

            return AirtimeResult(
                success=True,
                provider=self.name,
                reference=reference,
                message=message or "ORDER_RECEIVED",
                raw=data,
            )

        # Pending / on hold
        if statuscode in ("201", "600", "601") or status in (
            "PENDING",
            "ON_HOLD",
            "ON-HOLD",
            "PROCESSING",
        ):
            data["retryable"] = False
            data["provider_state"] = "pending"

            return AirtimeResult(
                success=True,
                provider=self.name,
                reference=reference,
                message=message or "Processing, please check back.",
                raw=data,
            )

        # Default hard failure
        data["retryable"] = False
        data["provider_state"] = "failed"
        data["failure_reason"] = status or "UNKNOWN_FAILURE"

        return AirtimeResult(
            success=False,
            provider=self.name,
            reference=reference,
            message=message or "Airtime request failed",
            raw=data,
        )

    async def check_status(
        self,
        reference: str,
    ) -> AirtimeResult:
        """
        ClubKonnect status checking is not implemented here yet.

        We deliberately do not invent a status/requery endpoint.
        VTU.ng will implement its own status/requery behavior later.
        """
        return AirtimeResult(
            success=False,
            provider=self.name,
            reference=reference,
            message="ClubKonnect status checking is not implemented.",
            raw={
                "provider_state": "unsupported",
                "retryable": False,
                "failure_reason": "STATUS_CHECK_UNSUPPORTED",
            },
        )


# -----------------------------------------------------------------------
# Backward-compatible function
# -----------------------------------------------------------------------
# Existing code may still import buy_airtime directly. Keep this wrapper
# temporarily so we don't break the current application while the provider
# abstraction is being introduced.
async def buy_airtime(
    phone: str,
    amount: int,
    network: Optional[str] = None,
    request_id: Optional[str] = None,
    callback_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Backward-compatible ClubKonnect function.

    The new provider architecture uses ClubKonnectProvider.send_airtime().
    This wrapper preserves the old function while migration is in progress.
    """
    provider = ClubKonnectProvider()

    result = await provider.send_airtime(
        phone=phone,
        amount=amount,
        request_id=request_id,
        network=network,
    )

    if result.raw is not None:
        return result.raw

    return {
        "status": "success" if result.success else "error",
        "reference": result.reference,
        "message": result.message,
    }