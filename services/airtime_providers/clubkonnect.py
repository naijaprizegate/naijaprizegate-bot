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

logger = logging.getLogger(__name__)

CK_USER_ID = os.getenv("CLUBKONNECT_USER_ID")
CK_API_KEY = os.getenv("CLUBKONNECT_API_KEY")
CK_BASE_URL = os.getenv("CLUBKONNECT_BASE_URL", "https://www.nellobytesystems.com").rstrip("/")

# Optional: let you enforce your own minimum (business rule), while provider minimum is 50
# If you want to force ₦100 in your bot, set CLUBKONNECT_MIN_AMOUNT=100 in Render env vars.
CK_MIN_AMOUNT = int(os.getenv("CLUBKONNECT_MIN_AMOUNT", "50"))

if not CK_USER_ID or not CK_API_KEY:
    raise RuntimeError("CLUBKONNECT_USER_ID / CLUBKONNECT_API_KEY not set")


# Network codes from ClubKonnect docs
NETWORK_CODE = {
    "mtn": "01",
    "glo": "02",
    "9mobile": "03",
    "airtel": "04",
}


def normalize_phone(phone: str) -> str:
    """
    Normalize to Nigerian format: 11 digits starting with 0 (e.g. 08012345678).
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
    Simple network guess by prefix. MVP-friendly.
    """
    p = normalize_phone(phone)

    MTN = ("0703", "0704", "0706", "0803", "0806", "0810", "0813", "0814", "0816", "0903", "0906", "0913", "0916")
    AIRTEL = ("0701", "0708", "0802", "0808", "0812", "0901", "0902", "0904", "0907", "0912")
    GLO = ("0705", "0805", "0807", "0811", "0815", "0905", "0915")
    ETISALAT = ("0809", "0817", "0818", "0908", "0909")

    if p.startswith(MTN):
        return "mtn"
    if p.startswith(AIRTEL):
        return "airtel"
    if p.startswith(GLO):
        return "glo"
    if p.startswith(ETISALAT):
        return "9mobile"
    return None


async def buy_airtime(
    phone: str,
    amount: int,
    network: Optional[str] = None,
    request_id: Optional[str] = None,
    callback_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    ClubKonnect/Nellobytes Buy Airtime API
    - Method: HTTPS GET
    - Endpoint: /APIAirtimeV1.asp (as shown in your dashboard)
    """
    p = normalize_phone(phone)

    # Basic phone validation (your bot already asks for 11-digit NG number)
    if len(p) != 11 or not p.startswith("0"):
        return {"status": "error", "message": "Invalid phone number. Use 11-digit Nigerian format e.g. 08012345678."}

    try:
        amt = int(amount)
    except Exception:
        return {"status": "error", "message": "Invalid amount."}

    # Provider minimum is 50 (per your screenshot), but you can enforce higher via env var
    if amt < CK_MIN_AMOUNT:
        return {"status": "error", "message": f"Minimum airtime amount is {CK_MIN_AMOUNT}"}

    net = (network or guess_network(p) or "").lower()

    # If we can't detect network automatically, tell the bot to ask the user
    if not net:
        return {
            "status": "need_network",
            "message": "Could not detect network. Please choose your network.",
            "phone": p,
            "amount": amt,
        }

    mobile_network_code = NETWORK_CODE.get(net)
    if not mobile_network_code:
        return {
            "status": "need_network",
            "message": "Unsupported/unknown network. Please choose your network.",
            "phone": p,
            "amount": amt,
        }


    rid = request_id or f"NP-{uuid.uuid4()}"

    params = {
        "UserID": CK_USER_ID,
        "APIKey": CK_API_KEY,
        "MobileNetwork": mobile_network_code,
        "Amount": str(amt),
        "MobileNumber": p,
        "RequestID": rid,
    }

    # Optional — only include if it’s a REAL public HTTPS endpoint
    if callback_url:
        params["CallBackURL"] = callback_url

    url = f"{CK_BASE_URL}/APIAirtimeV1.asp"

    # IMPORTANT: don't log API key or full URL with params
    logger.info(f"ClubKonnect airtime request | phone={p} amount={amt} network={net} request_id={rid}")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, params=params)
    except Exception as e:
        return {"status": "error", "message": f"Provider request failed: {e.__class__.__name__}"}

    # Parse JSON response
    try:
        data = resp.json()
        if not isinstance(data, dict):
            return {"status": "error", "message": "Unexpected provider response format", "raw": str(data)[:300]}
    except Exception:
        data = {
            "status": "error",
            "message": "Non-JSON response from provider",
            "http_status": resp.status_code,
            "raw": (resp.text or "")[:300],
        }

    # Helpful log (no sensitive data)
    logger.info(
        "ClubKonnect airtime response | request_id=%s status=%s statuscode=%s",
        rid,
        data.get("status"),
        data.get("statuscode") or data.get("statusCode"),
    )

    return data
