# ====================================================
# utils/signer.py
# ====================================================

import os
import json
import hmac
import hashlib
import base64
import time
from typing import Tuple, Optional

WINNER_SIGNING_KEY = os.getenv("WINNER_SIGNING_KEY")  # REQUIRED

if not WINNER_SIGNING_KEY:
    raise RuntimeError("WINNER_SIGNING_KEY environment variable is required for signed winner links")

KEY_BYTES = WINNER_SIGNING_KEY.encode("utf-8")


def _b64url_encode(b: bytes) -> str:
    s = base64.urlsafe_b64encode(b).decode("ascii")
    return s.rstrip("=")


def _b64url_decode(s: str) -> bytes:
    # Add padding back
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _hmac_sig(data: bytes) -> str:
    sig = hmac.new(KEY_BYTES, data, digestmod=hashlib.sha256).digest()
    return _b64url_encode(sig)


def generate_signed_token(tgid: int, choice: str, expires_seconds: int = 3600) -> str:
    """
    Return a URL-safe token encoding {tgid, choice, exp}.
    expires_seconds: TTL in seconds (default 1 hour).
    """
    payload = {
        "tgid": int(tgid),
        "choice": str(choice),
        "exp": int(time.time()) + int(expires_seconds),
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_b64 = _b64url_encode(payload_bytes)
    sig = _hmac_sig(payload_bytes)
    token = f"{payload_b64}.{sig}"
    return token


def verify_signed_token(token: str) -> Tuple[bool, Optional[dict], str]:
    """
    Returns tuple: (ok, payload_dict_or_none, error_message)
    Do not raise exceptions for invalid tokens — return false + message.
    """
    try:
        if "." not in token:
            return False, None, "Invalid token format"

        payload_b64, sig_provided = token.split(".", 1)
        payload_bytes = _b64url_decode(payload_b64)
    except Exception as e:
        return False, None, "Malformed token"

    # verify signature
    expected_sig = _hmac_sig(payload_bytes)
    # Use hmac.compare_digest to avoid timing attacks
    if not hmac.compare_digest(expected_sig, sig_provided):
        return False, None, "Invalid signature"

    # parse payload
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except Exception:
        return False, None, "Invalid payload"

    # expiry check
    now = int(time.time())
    if payload.get("exp", 0) < now:
        return False, None, "Token expired"

    # minimal validation
    if "tgid" not in payload or "choice" not in payload:
        return False, None, "Token missing required fields"

    return True, payload, ""
