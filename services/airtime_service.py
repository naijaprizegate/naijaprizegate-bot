# ==========================================================================
# services/airtime_service.py
# Flutterwave Bills API (Airtime - All Nigerian networks)
# ==========================================================================
from __future__ import annotations

import os
import uuid
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

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
# Single airtime payout processor — Flutterwave Bills API
# ===================================================================
async def process_single_airtime_payout(
    session: AsyncSession,
    payout_id: str,
    bot,
    admin_id: int,
) -> None:
    """
    Loads a pending AIRTIME payout row → Calls Flutterwave → Updates DB →
    Sends Telegram alerts to user + admin

    Safe for background worker loops.
    """

    if AIRTIME_PROVIDER.lower() != "flutterwave":
        logger.warning(f"⚠️ Unsupported airtime provider configured: {AIRTIME_PROVIDER}")
        return

    # ------------------------------------------------------------------
    # DB — Lock row FOR UPDATE inside transaction
    # ------------------------------------------------------------------
    async with session.begin():
        res = await session.execute(
            text(
                """
                SELECT id, user_id, tg_id, phone_number, amount
                FROM airtime_payouts
                WHERE id = :pid AND status = 'pending'
                FOR UPDATE
                """
            ),
            {"pid": payout_id},
        )
        row = res.first()

        if not row:
            logger.info(f"ℹ️ No pending payout found for {payout_id}")
            return

        row_map = row._mapping
        phone: str = row_map["phone_number"]
        amount: int = row_map["amount"]
        tg_id: int = row_map["tg_id"]

        logger.info(f"🚀 Processing payout {payout_id}: ₦{amount} → {phone} (TG {tg_id})")

        # ------------------------------------------------------------------
        # Call Flutterwave API
        # ------------------------------------------------------------------
        try:
            fw_data = await call_flutterwave_airtime(phone, amount)
            status = str(fw_data.get("status", "")).lower()

            data = fw_data.get("data") or {}
            fw_ref: Optional[str] = (
                data.get("reference")
                or data.get("flw_ref")
                or data.get("tx_ref")
            )

            provider_json = json.dumps(fw_data)

            # ==============================================================
            # SUCCESS
            # ==============================================================
            if status == "success":
                await session.execute(
                    text(
                        """
                        UPDATE airtime_payouts
                        SET status = 'completed',
                            flutterwave_tx_ref = :tx,
                            provider_response = :resp::jsonb,
                            completed_at = :now
                        WHERE id = :pid
                        """
                    ),
                    {
                        "pid": payout_id,
                        "tx": fw_ref,
                        "resp": provider_json,
                        "now": datetime.now(timezone.utc),
                    },
                )

                masked_phone = phone[:-4].rjust(len(phone), "•")

                # 🎉 Notify user
                try:
                    await bot.send_message(
                        chat_id=tg_id,
                        text=(
                            "🎉 *Airtime Reward Credited!*\n\n"
                            f"📱 `{masked_phone}`\n"
                            f"💸 *₦{amount}*\n\n"
                            "Keep playing and winning on *NaijaPrizeGate*! 🔥"
                        ),
                        parse_mode="Markdown",
                    )
                except Exception as e:
                    logger.error(f"⚠️ Failed notifying user: {e}")

                #  👑 Notify admin
                try:
                    await bot.send_message(
                        chat_id=admin_id,
                        text=(
                            "📲 *Airtime AUTO-CREDITED via Flutterwave*\n\n"
                            f"TG ID: `{tg_id}`\n"
                            f"Phone: `{phone}`\n"
                            f"Amount: ₦{amount}\n"
                            f"FW Ref: `{fw_ref}`\n"
                        ),
                        parse_mode="Markdown",
                    )
                except Exception as e:
                    logger.error(f"⚠️ Failed notifying admin: {e}")

                logger.info(f"✅ Airtime payout {payout_id} completed.")
                return

            # ==============================================================
            # FAIL (Unhandled FW Status)
            # ==============================================================
            await session.execute(
                text(
                    """
                    UPDATE airtime_payouts
                    SET status = 'failed',
                        provider_response = :resp::jsonb
                    WHERE id = :pid
                    """
                ),
                {"pid": payout_id, "resp": provider_json},
            )

            logger.warning(f"⚠️ Airtime payout failed: FW status={status}, data={fw_data}")

            # Notify admin about failure
            try:
                await bot.send_message(
                    admin_id,
                    (
                        "⚠️ *Airtime payout FAILED*\n\n"
                        f"ID: `{payout_id}`\n"
                        f"Phone: `{phone}`\n"
                        f"Amount: ₦{amount}\n"
                        f"Flutterwave status: `{status}`"
                    ),
                    parse_mode="Markdown",
                )
            except Exception:
                pass

        except Exception as e:
            # ==============================================================
            # EXCEPTION
            # ==============================================================
            logger.error(f"❌ Exception while processing payout {payout_id}: {e}")

            await session.execute(
                text(
                    """
                    UPDATE airtime_payouts
                    SET status='failed'
                    WHERE id=:pid
                    """
                ),
                {"pid": payout_id},
            )

            try:
                await bot.send_message(
                    admin_id,
                    (
                        "🚨 *Airtime payout EXCEPTION*\n\n"
                        f"Payout ID: `{payout_id}`\n"
                        f"Phone: `{phone}`\n"
                        f"Amount: ₦{amount}`\n"
                        f"Error: `{e}`"
                    ),
                    parse_mode="Markdown",
                )
            except Exception:
                pass
