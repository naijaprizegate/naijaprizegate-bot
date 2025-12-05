# ==========================================================================
# services/airtime_service.py
# Flutterwave Bills API (Airtime - All Nigerian networks)
# ==========================================================================
from __future__ import annotations

import os
import uuid
import json
import httpx
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from telegram import Bot

from logger import logger

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# ENV + Constants
# -------------------------------------------------------------------
FLW_SECRET_KEY = os.getenv("FLW_SECRET_KEY")  # Required
FLW_BASE_URL = os.getenv("FLW_BASE_URL", "https://api.flutterwave.com")
AIRTIME_PROVIDER = os.getenv("AIRTIME_PROVIDER", "flutterwave")

if not FLW_SECRET_KEY:
    raise RuntimeError("❌ FLW_SECRET_KEY not set in environment variables.")


BILLS_ENDPOINT = f"{FLW_BASE_URL.rstrip('/')}/v3/bills"
COUNTRY = "NG"
CURRENCY = "NGN"  # Reserved for future usage


# -------------------------------------------------------------------
# Network prefix detection (fallback to FW if unknown)
# -------------------------------------------------------------------
NETWORK_PREFIXES = {
    "MTN": (
        "234703", "234706", "234803", "234806", "234810",
        "234813", "234814", "234816", "234903", "234906",
        "234913", "234916"
    ),
    "AIRTEL": (
        "234701", "234708", "234802", "234808", "234812",
        "234902", "234907", "234908", "234912"
    ),
    "GLO": (
        "234705", "234805", "234807", "234811", "234815",
        "234905"
    ),
    "9MOBILE": (
        "234809", "234817", "234818", "234909"
    ),
}


def normalize_msisdn(raw: str) -> str:
    """
    Normalize into 234XXXXXXXXXX format.
    """
    number = raw.strip().replace(" ", "").replace("-", "")

    if number.startswith("+"):
        number = number[1:]

    if number.startswith("0") and len(number) == 11:
        number = "234" + number[1:]

    return number


def detect_network(msisdn_234: str) -> Optional[str]:
    """
    Prefix-based detection for better logging.
    """
    for net, prefixes in NETWORK_PREFIXES.items():
        if any(msisdn_234.startswith(p) for p in prefixes):
            return net
    return None


# -------------------------------------------------------------------
# Low-level: Call Flutterwave Airtime API
# -------------------------------------------------------------------
async def call_flutterwave_airtime(phone_number: str, amount: int) -> Dict[str, Any]:
    """
    Executes an Airtime top-up via Flutterwave Bills API.
    """

    msisdn = normalize_msisdn(phone_number)
    network = detect_network(msisdn)  # For logs only
    reference = f"NPGAIRTIME-{uuid.uuid4().hex}"

    payload: Dict[str, Any] = {
        "country": COUNTRY,
        "customer": msisdn,
        "amount": amount,
        "type": "AIRTIME",
        "reference": reference,
    }

    headers = {
        "Authorization": f"Bearer {FLW_SECRET_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    logger.info(
        f"🌍 FW Airtime → {msisdn} ₦{amount} | ref={reference} "
        f"(network={network or 'AUTO'})"
    )

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(BILLS_ENDPOINT, json=payload, headers=headers)

    try:
        data: Dict[str, Any] = resp.json()
    except Exception:
        data = {
            "status": "error",
            "error": "Non-JSON response from Flutterwave",
            "raw": resp.text,
        }

    # Ensure reference exists in the stored record
    if isinstance(data.get("data"), dict):
        data["data"].setdefault("reference", reference)
    else:
        data["data"] = {"reference": reference}

    status = data.get("status")
    code = resp.status_code

    if code >= 400 or str(status).lower() != "success":
        logger.error(
            f"❌ FW Airtime FAILED [HTTP {code}] → {msisdn} | data={data}"
        )
    else:
        logger.info(
            f"✅ FW Airtime ACCEPTED → {msisdn} | ref={reference} | data={data}"
        )

    return data


# ===================================================================
# Single airtime payout processor — Flutterwave Bills API (Patched)
# ===================================================================

MAX_RETRY = 5   # prevents infinite retry loops

async def process_single_airtime_payout(
    session: AsyncSession,
    payout_id: str,
    bot: Optional[Bot],
    admin_id: int,
) -> None:

    if AIRTIME_PROVIDER.lower() != "flutterwave":
        logger.warning(f"⚠️ Unsupported airtime provider configured: {AIRTIME_PROVIDER}")
        return

    # Create bot instance if missing
    if bot is None:
        try:
            bot = Bot(token=BOT_TOKEN)
        except Exception as e:
            logger.error(f"❌ Failed to init Bot instance: {e}")
            return

    async with session.begin():
        res = await session.execute(
            text("""
                SELECT id, user_id, tg_id, phone_number, amount, retry_count
                FROM airtime_payouts
                WHERE id = :pid AND status = 'pending'
                FOR UPDATE
            """),
            {"pid": payout_id},
        )
        row = res.first()

        if not row:
            logger.info(f"ℹ️ No pending payout for {payout_id}")
            return

        row_map = row._mapping
        phone = row_map["phone_number"]
        amount = row_map["amount"]
        tg_id = row_map["tg_id"]
        retry_count = row_map["retry_count"] or 0

        if not phone:
            logger.warning(f"📵 No phone number for payout {payout_id}")
            await session.execute(
                text("""
                    UPDATE airtime_payouts
                    SET status='pending_phone',
                        last_retry_at=NULL
                    WHERE id=:pid
                """),
                {"pid": payout_id}
            )
            return

        if retry_count >= MAX_RETRY:
            logger.error(f"🚫 Retry limit reached for {payout_id}")
            await session.execute(
                text("""
                    UPDATE airtime_payouts
                    SET status='failed',
                        last_retry_at=NOW()
                    WHERE id=:pid
                """),
                {"pid": payout_id}
            )
            return

        logger.info(f"🚀 Processing payout {payout_id}: ₦{amount} → {phone}")

        try:
            fw_data = await call_flutterwave_airtime(phone, amount)
            status = str(fw_data.get("status", "")).lower()
            data = fw_data.get("data") or {}
            fw_ref = data.get("reference") or data.get("tx_ref")
            provider_json = json.dumps(fw_data)

            # Save JSONB safely
            def update_status(new_status):
                return session.execute(
                    text("""
                        UPDATE airtime_payouts
                        SET status=:sts,
                            provider_response = CAST(:resp AS JSONB),
                            retry_count = retry_count + 1,
                            last_retry_at = NOW()
                        WHERE id=:pid
                    """),
                    {"pid": payout_id, "resp": provider_json, "sts": new_status},
                )

            # SUCCESS
            if status == "success":
                await session.execute(
                    text("""
                        UPDATE airtime_payouts
                        SET status='completed',
                            flutterwave_tx_ref=:tx,
                            provider_response = CAST(:resp AS JSONB),
                            completed_at=NOW()
                        WHERE id=:pid
                    """),
                    {"pid": payout_id, "tx": fw_ref, "resp": provider_json},
                )

                masked = phone[:-4].rjust(len(phone), "•")
                try:
                    await bot.send_message(
                        tg_id,
                        (
                            "🎉 *Airtime Reward Sent!*\n\n"
                            f"📱 `{masked}`\n"
                            f"💸 *₦{amount}*\n\n"
                            "Keep playing & winning! 🔥"
                        ),
                        parse_mode="Markdown",
                    )
                except Exception:
                    pass

                await bot.send_message(
                    admin_id,
                    f"📲 Airtime sent\nPhone: `{phone}`\nAmount: ₦{amount}\nRef: `{fw_ref}`",
                    parse_mode="Markdown"
                )

                logger.info(f"💚 Completed Airtime | ID {payout_id}")
                return

            # FAILURE FROM API
            await update_status("failed")
            logger.warning(f"⚠️ FW returned failure for {payout_id}: {status}")

            await bot.send_message(
                admin_id,
                f"⚠️ Airtime FAILED\nPhone: `{phone}`\nAmount: ₦{amount}\nFW Status: `{status}`",
                parse_mode="Markdown"
            )
            return

        except Exception as e:
            err = str(e).lower()
            logger.error(f"❌ Exception in payout {payout_id}: {e}")

            if "whitelist" in err:
                await session.execute(
                    text("""
                        UPDATE airtime_payouts
                        SET status='ip_blocked',
                            last_retry_at=NOW()
                        WHERE id=:pid
                    """),
                    {"pid": payout_id},
                )
                logger.critical("🚫 Flutterwave blocking API — IP whitelisting required")
            else:
                await session.execute(
                    text("""
                        UPDATE airtime_payouts
                        SET status='failed',
                            last_retry_at=NOW()
                        WHERE id=:pid
                    """),
                    {"pid": payout_id},
                )

            try:
                await bot.send_message(
                    admin_id,
                    (
                        "🚨 *Airtime payout EXCEPTION*\n"
                        f"ID: `{payout_id}`\n"
                        f"Phone: `{phone}`\n"
                        f"Amount: ₦{amount}\n"
                        f"Error: `{e}`"
                    ),
                    parse_mode="Markdown",
                )
            except:
                pass
