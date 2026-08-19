# =======================================================
# services/flutterwave_client.py
# ========================================================
import os
import hmac
import uuid
import logging
from typing import Any, Optional

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("flutterwave_client")
logger.setLevel(logging.INFO)

FLW_BASE_URL = os.getenv("FLW_BASE_URL", "https://api.flutterwave.com/v3")
FLW_SECRET_KEY = os.getenv("FLW_SECRET_KEY")
FLW_SECRET_HASH = os.getenv("FLW_SECRET_HASH")
WEBHOOK_REDIRECT_URL = os.getenv(
    "WEBHOOK_REDIRECT_URL",
    "https://naijaprizegate-bot.fly.dev/flw/redirect",
)

TRIVIA_ALLOWED_PACKAGES = {50, 500, 1000}
JAMB_ALLOWED_PACKAGES = {100, 200, 300, 400}
WAEC_ALLOWED_PACKAGES = {100, 200, 300, 400}
MOCKJAMB_ALLOWED_PACKAGES = {100}
MOCKWAEC_ALLOWED_PACKAGES = {100}
JAMBMOCKSUBJECT_ALLOWED_PACKAGES = {100, 200, 300, 400, 500}
WAECMOCKSUBJECT_ALLOWED_PACKAGES = {100, 200, 300, 400, 500}

PRICE_TO_TRIES = {
    50: 1,
    500: 15,
    1000: 35,
}

JAMB_PRICE_TO_CREDITS = {
    100: 50,
    200: 100,
    300: 150,
    400: 200,
}


def calculate_tries(amount: int) -> int:
    if not isinstance(amount, int) or amount <= 0:
        return 0
    if amount in PRICE_TO_TRIES:
        return PRICE_TO_TRIES[amount]
    return max(1, amount // 100)


def calculate_jamb_credits(amount: int) -> int:
    if not isinstance(amount, int) or amount <= 0:
        return 0
    return JAMB_PRICE_TO_CREDITS.get(amount, 0)

def calculate_waec_credits(amount: int) -> int:
    return calculate_jamb_credits(amount)

def normalize_flw_status(raw_status: Optional[str]) -> str:
    status = (raw_status or "").lower().strip()

    if status in ("successful", "success", "completed"):
        return "successful"
    if status in ("failed",):
        return "failed"
    if status in ("expired", "cancelled", "canceled"):
        return "expired"
    if status in ("not_found",):
        return "not_found"
    if status in ("error",):
        return "error"
    if not status:
        return "pending"
    return status


def validate_flutterwave_webhook(headers: dict, raw_body: str) -> bool:
    signature = headers.get("verif-hash")
    if not signature:
        logger.warning("⚠️ Flutterwave webhook missing verif-hash header")
        return False
    if not FLW_SECRET_HASH:
        logger.warning("⚠️ FLW_SECRET_HASH is not set in environment")
        return False
    return hmac.compare_digest(signature, FLW_SECRET_HASH)

def build_tx_ref(product_type: str) -> str:
    prefix = product_type.upper().strip()
    return f"{prefix}-{uuid.uuid4().hex[:12].upper()}"


async def create_checkout(
    *,
    session: AsyncSession | None = None,
    user_id: int,
    amount: int,
    username: str | None = None,
    email: str | None = None,
    tx_ref: str | None = None,
    meta: dict | None = None,
    product_type: str = "TRIVIA",
) -> str | None:
    """
    Shared checkout creator.
    DB inserts for pending rows should be done by the product-specific service
    before calling this.
    """
    del session  # reserved for compatibility with your old signature

    if not FLW_SECRET_KEY:
        logger.error("❌ Missing FLW_SECRET_KEY in environment")
        return None

    if not WEBHOOK_REDIRECT_URL.startswith("https://"):
        logger.error("❌ WEBHOOK_REDIRECT_URL must be https: %s", WEBHOOK_REDIRECT_URL)
        return None

    product_type = product_type.upper().strip()
    amount = int(amount)

    if product_type == "TRIVIA" and amount not in TRIVIA_ALLOWED_PACKAGES:
        logger.warning("🚫 Invalid TRIVIA amount=%s user_id=%s", amount, user_id)
        return None

    if product_type == "JAMB" and amount not in JAMB_ALLOWED_PACKAGES:
        logger.warning("🚫 Invalid JAMB amount=%s user_id=%s", amount, user_id)
        return None

    if product_type == "WAEC" and amount not in WAEC_ALLOWED_PACKAGES:
        logger.warning("🚫 Invalid WAEC amount=%s user_id=%s", amount, user_id)
        return None
    
    if product_type == "MOCKJAMB" and amount not in MOCKJAMB_ALLOWED_PACKAGES:
        logger.warning("🚫 Invalid MOCKJAMB amount=%s user_id=%s", amount, user_id)
        return None

    if product_type == "MOCKWAEC" and amount not in MOCKWAEC_ALLOWED_PACKAGES:
        logger.warning("🚫 Invalid MOCKWAEC amount=%s user_id=%s", amount, user_id)
        return None
    
    if product_type == "JAMBMOCKSUBJECT" and amount not in JAMBMOCKSUBJECT_ALLOWED_PACKAGES:
        logger.warning("🚫 Invalid JAMBMOCKSUBJECT amount=%s user_id=%s", amount, user_id)
        return None

    if product_type == "WAECMOCKSUBJECT" and amount not in WAECMOCKSUBJECT_ALLOWED_PACKAGES:
        logger.warning("🚫 Invalid WAECMOCKSUBJECT amount=%s user_id=%s", amount, user_id)
        return None
    
    if product_type not in {
        "TRIVIA",
        "JAMB",
        "WAEC",
        "MOCKJAMB",
        "MOCKWAEC",
        "JAMBMOCKSUBJECT",
        "WAECMOCKSUBJECT",
    }:
        logger.warning("🚫 Unknown product_type=%s user_id=%s", product_type, user_id)
        return None

    return await create_flutterwave_checkout_link(
        tg_id=user_id,
        amount=amount,
        username=username,
        email=email,
        tx_ref=tx_ref or build_tx_ref(product_type),
        meta=meta or {},
        product_type=product_type,
    )


async def create_flutterwave_checkout_link(
    *,
    tg_id: int,
    amount: int,
    username: str | None = None,
    email: str | None = None,
    tx_ref: str,
    meta: dict | None = None,
    product_type: str,
) -> str | None:
    """
    Only talks to Flutterwave. No DB writes here.
    """
    if not FLW_SECRET_KEY:
        logger.error("❌ Missing FLW_SECRET_KEY")
        return None

    redirect_url = f"{WEBHOOK_REDIRECT_URL}?tx_ref={tx_ref}"

    payload = {
        "tx_ref": tx_ref,
        "amount": amount,
        "currency": "NGN",
        "redirect_url": redirect_url,
        "payment_options": "card,banktransfer,ussd",
        "customer": {
            "email": email or f"user{tg_id}@naijaprizegate.local",
            "name": username or f"TG-{tg_id}",
        },
        "customizations": {
            "title": "NaijaPrizeGate",
            "description": f"{product_type} purchase",
            "logo": "",
        },
        "meta": {
            "tg_id": str(tg_id),
            "username": username or "",
            "product_type": product_type,
            **(meta or {}),
        },
    }

    headers = {
        "Authorization": f"Bearer {FLW_SECRET_KEY}",
        "Content-Type": "application/json",
    }

    timeout = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=30.0)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{FLW_BASE_URL}/payments",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.ReadTimeout:
        logger.exception(
            "❌ Flutterwave checkout request timed out | product_type=%s | tx_ref=%s",
            product_type,
            tx_ref,
        )
        return None
    except Exception:
        logger.exception(
            "❌ Flutterwave checkout request failed | product_type=%s | tx_ref=%s",
            product_type,
            tx_ref,
        )
        return None

    payment_link = (((data or {}).get("data") or {}).get("link") or "").strip()
    if not payment_link:
        logger.error(
            "❌ Flutterwave checkout response missing link | product_type=%s | tx_ref=%s | body=%s",
            product_type,
            tx_ref,
            str(data)[:500],
        )
        return None

    logger.info(
        "🟢 Flutterwave checkout created | product_type=%s | tx_ref=%s | redirect_url=%s",
        product_type,
        tx_ref,
        redirect_url,
    )
    return payment_link


async def verify_payment(tx_ref: str) -> dict[str, Any]:
    """
    Returns flat dict:
    {
        status, amount, tx_ref, flw_tx_id, meta
    }
    No DB crediting here.
    """
    if not FLW_SECRET_KEY:
        return {"status": "error", "error": "missing FLW_SECRET_KEY"}

    headers = {"Authorization": f"Bearer {FLW_SECRET_KEY}"}
    timeout = httpx.Timeout(connect=10.0, read=20.0, write=20.0, pool=20.0)

    try:
        lookup_url = f"{FLW_BASE_URL}/transactions?tx_ref={tx_ref}"
        async with httpx.AsyncClient(timeout=timeout) as client:
            lookup_resp = await client.get(lookup_url, headers=headers)
            lookup_resp.raise_for_status()
            lookup_data = lookup_resp.json()

        data_list = lookup_data.get("data") or []
        if not data_list:
            return {"status": "not_found", "tx_ref": tx_ref}

        tx_id = data_list[0].get("id")
        if not tx_id:
            return {"status": "invalid", "tx_ref": tx_ref}

        verify_url = f"{FLW_BASE_URL}/transactions/{tx_id}/verify"
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(verify_url, headers=headers)
            resp.raise_for_status()
            fw_resp = resp.json()

    except Exception as e:
        logger.exception("❌ verify_payment error for %s: %s", tx_ref, e)
        return {"status": "error", "tx_ref": tx_ref, "error": str(e)}

    tx_data = fw_resp.get("data") or {}
    return {
        "status": normalize_flw_status(tx_data.get("status")),
        "amount": int(tx_data.get("amount") or 0),
        "tx_ref": tx_ref,
        "flw_tx_id": tx_data.get("id"),
        "meta": tx_data.get("meta") or {},
    }


# =======================================================
# Bank Transfer / Payout
# =======================================================

async def create_bank_transfer(
    *,
    account_bank: str,
    account_number: str,
    amount: int,
    beneficiary_name: str,
    reference: str,
    narration: str,
    callback_url: str | None = None,
    meta: dict | None = None,
) -> dict[str, Any]:
    """
    Initiates a NGN bank transfer through Flutterwave.

    Provider-level function only:
    - No database writes.
    - Does not mark any withdrawal as completed.
    - Returns a normalized response.

    The caller is responsible for deciding what the
    provider response means for the Finance workflow.
    """

    if not FLW_SECRET_KEY:
        logger.error("❌ Missing FLW_SECRET_KEY")
        return {
            "success": False,
            "status": "error",
            "error": "missing FLW_SECRET_KEY",
        }

    if not account_bank:
        return {
            "success": False,
            "status": "error",
            "error": "missing account_bank",
        }

    if not account_number:
        return {
            "success": False,
            "status": "error",
            "error": "missing account_number",
        }

    if amount <= 0:
        return {
            "success": False,
            "status": "error",
            "error": "invalid amount",
        }

    if not beneficiary_name:
        return {
            "success": False,
            "status": "error",
            "error": "missing beneficiary_name",
        }

    if not reference:
        return {
            "success": False,
            "status": "error",
            "error": "missing reference",
        }

    payload = {
        "account_bank": str(account_bank),
        "account_number": str(account_number),
        "amount": int(amount),
        "currency": "NGN",
        "beneficiary_name": beneficiary_name,
        "reference": reference,
        "narration": narration,
    }

    if callback_url:
        payload["callback_url"] = callback_url

    if meta:
        payload["meta"] = meta

    headers = {
        "Authorization": f"Bearer {FLW_SECRET_KEY}",
        "Content-Type": "application/json",
    }

    timeout = httpx.Timeout(
        connect=10.0,
        read=30.0,
        write=30.0,
        pool=30.0,
    )

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{FLW_BASE_URL}/transfers",
                json=payload,
                headers=headers,
            )

        response.raise_for_status()
        data = response.json()

    except httpx.ReadTimeout:
        logger.exception(
            "❌ Flutterwave transfer timed out | reference=%s",
            reference,
        )
        return {
            "success": False,
            "status": "timeout",
            "reference": reference,
            "error": "Flutterwave transfer request timed out.",
        }

    except httpx.HTTPStatusError as exc:
        body = exc.response.text[:1000]

        logger.error(
            "❌ Flutterwave transfer HTTP error | "
            "reference=%s | status=%s | body=%s",
            reference,
            exc.response.status_code,
            body,
        )

        return {
            "success": False,
            "status": "http_error",
            "reference": reference,
            "http_status": exc.response.status_code,
            "error": body,
        }

    except Exception as exc:
        logger.exception(
            "❌ Flutterwave transfer request failed | reference=%s",
            reference,
        )

        return {
            "success": False,
            "status": "error",
            "reference": reference,
            "error": str(exc),
        }

    transfer_data = data.get("data") or {}

    provider_status = normalize_flw_status(
        transfer_data.get("status")
    )

    provider_reference = (
        transfer_data.get("reference")
        or reference
    )

    transfer_id = transfer_data.get("id")

    provider_success = (
        str(data.get("status") or "").lower() == "success"
    )

    logger.info(
        "🟢 Flutterwave transfer response | "
        "reference=%s | transfer_id=%s | status=%s",
        reference,
        transfer_id,
        provider_status,
    )

    return {
        "success": provider_success,
        "status": provider_status,
        "reference": provider_reference,
        "transfer_id": transfer_id,
        "message": data.get("message"),
        "raw": data,
    }


async def get_bank_transfer(
    *,
    transfer_id: str,
) -> dict[str, Any]:
    """
    Retrieves a Flutterwave transfer by provider transfer ID.

    Provider-level function only.
    Does not modify Finance records.
    """

    if not FLW_SECRET_KEY:
        logger.error("❌ Missing FLW_SECRET_KEY")
        return {
            "success": False,
            "status": "error",
            "error": "missing FLW_SECRET_KEY",
        }

    if not transfer_id:
        return {
            "success": False,
            "status": "error",
            "error": "missing transfer_id",
        }

    headers = {
        "Authorization": f"Bearer {FLW_SECRET_KEY}",
    }

    timeout = httpx.Timeout(
        connect=10.0,
        read=20.0,
        write=20.0,
        pool=20.0,
    )

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                f"{FLW_BASE_URL}/transfers/{transfer_id}",
                headers=headers,
            )

        response.raise_for_status()
        data = response.json()

    except httpx.ReadTimeout:
        logger.exception(
            "❌ Flutterwave transfer lookup timed out | transfer_id=%s",
            transfer_id,
        )
        return {
            "success": False,
            "status": "timeout",
            "transfer_id": transfer_id,
            "error": "Flutterwave transfer lookup timed out.",
        }

    except httpx.HTTPStatusError as exc:
        body = exc.response.text[:1000]

        logger.error(
            "❌ Flutterwave transfer lookup HTTP error | "
            "transfer_id=%s | status=%s | body=%s",
            transfer_id,
            exc.response.status_code,
            body,
        )

        return {
            "success": False,
            "status": "http_error",
            "transfer_id": transfer_id,
            "http_status": exc.response.status_code,
            "error": body,
        }

    except Exception as exc:
        logger.exception(
            "❌ Flutterwave transfer lookup failed | transfer_id=%s",
            transfer_id,
        )

        return {
            "success": False,
            "status": "error",
            "transfer_id": transfer_id,
            "error": str(exc),
        }

    transfer_data = data.get("data") or {}

    return {
        "success": str(data.get("status") or "").lower() == "success",
        "status": normalize_flw_status(
            transfer_data.get("status")
        ),
        "transfer_id": transfer_data.get("id") or transfer_id,
        "reference": transfer_data.get("reference"),
        "amount": transfer_data.get("amount"),
        "currency": transfer_data.get("currency"),
        "raw": data,
    }
