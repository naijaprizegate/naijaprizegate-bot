# ==========================================================
# services/airtime_providers/clubkonnect.py
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
CK_BASE_URL = os.getenv("CLUBKONNECT_BASE_URL", "https://www.nellobytesystems.com")

if not CK_USER_ID or not CK_API_KEY:
    raise RuntimeError("CLUBKONNECT_USER_ID / CLUBKONNECT_API_KEY not set")


# Network codes from Clubkonnect docs
NETWORK_CODE = {
    "mtn": "01",
    "glo": "02",
    "9mobile": "03",
    "airtel": "04",
}


def guess_network(phone: str) -> Optional[str]:
    """
    Very simple network guess by prefix.
    You can improve this later. For now this is enough for MVP.
    """
    p = phone.strip()
    if p.startswith("+234"):
        p = "0" + p[4:]
    p = "".join(ch for ch in p if ch.isdigit())

    # Common Nigerian prefixes (not exhaustive, but good enough to start)
    MTN = ("0703", "0706", "0803", "0806", "0810", "0813", "0814", "0816", "0903", "0906", "0913", "0916")
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
    Calls Nellobytes/Clubkonnect Airtime API (GET) and returns JSON response.
    """
    if amount < 50:
        return {"status": "error", "message": "Minimum airtime amount is 50"}

    net = network or guess_network(phone)
    if not net:
        return {"status": "error", "message": "Could not detect network. Please choose network."}

    mobile_network_code = NETWORK_CODE.get(net.lower())
    if not mobile_network_code:
        return {"status": "error", "message": f"Unsupported network: {net}"}

    rid = request_id or f"NP-{uuid.uuid4()}"

    params = {
        "UserID": CK_USER_ID,
        "APIKey": CK_API_KEY,
        "MobileNetwork": mobile_network_code,
        "Amount": str(amount),
        "MobileNumber": phone,
        "RequestID": rid,
    }
    if callback_url:
        params["CallBackURL"] = callback_url

    url = f"{CK_BASE_URL}/APIAirtimeV1.asp"

    # IMPORTANT: don't log API key or full URL
    logger.info(f"Clubkonnect airtime request | phone={phone} amount={amount} network={net} request_id={rid}")

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(url, params=params)

    try:
        data = resp.json()
    except Exception:
        data = {"status": "error", "message": "Non-JSON response from provider", "raw": resp.text[:200]}

    # Do not log full provider payload (may contain sensitive fields)
    logger.info(f"Clubkonnect airtime response | request_id={rid} status={data.get('status') or data.get('statuscode')}")

    return data

