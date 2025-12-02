# ==========================================================================
# services/airtime_service.py
# ==========================================================================
import os
import uuid
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

logger = logging.getLogger(__name__)

FLW_SECRET_KEY = os.getenv("FLW_SECRET_KEY")  # same key you use for payments
FLW_BASE_URL = os.getenv("FLW_BASE_URL", "https://api.flutterwave.com")

# Optional: in case you want a separate env toggle later
AIRTIME_PROVIDER = os.getenv("AIRTIME_PROVIDER", "flutterwave")


# -------------------------------------------------------------------
# Low-level: Call Flutterwave Airtime/Bills API
# -------------------------------------------------------------------
async def call_flutterwave_airtime(phone: str, amount: int) -> Dict[str, Any]:
    """
    Calls Flutterwave Bills/Airtime API to credit airtime.
    Uses LIVE mode (production secret key).
    Returns the parsed JSON response.

    Raises:
        RuntimeError if FLW_SECRET_KEY is missing
        httpx.HTTPError on network issues
    """
    if not FLW_SECRET_KEY:
        raise RuntimeError("FLW_SECRET_KEY not set in environment")

    reference = f"NPGAIRTIME-{uuid.uuid4()}"  # unique ref for traceability

    # NOTE: This payload shape follows Flutterwave Bills v3 pattern.
    # You MUST adjust biller/bill_code to match the exact airtime product
    # you configured on Flutterwave.
    payload: Dict[str, Any] = {
        "country": "NG",
        "customer": phone,          # Flutterwave often uses "customer" for bills
        "amount": amount,
        "type": "AIRTIME",          # some integrations use specific type or biller code
        "reference": reference,
        # If your account uses biller/bill_code instead, replace appropriately:
        # "biller_name": "AIRTIME",
        # "bill_code": "BIL076",
    }

    headers = {
        "Authorization": f"Bearer {FLW_SECRET_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    url = f"{FLW_BASE_URL}/v3/bills"

    logger.info(f"📲 Sending Flutterwave airtime request to {phone} for ₦{amount}")

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(url, json=payload, headers=headers)

    try:
        data: Dict[str, Any] = resp.json()
    except Exception:
        # If Flutterwave returns non-JSON for any reason
        data = {"raw_text": resp.text, "status_code": resp.status_code}

    logger.info(f"📲 Flutterwave airtime response for {phone}: {data}")
    return data


# -------------------------------------------------------------------
# Single payout processor
# -------------------------------------------------------------------
async def process_single_airtime_payout(
    session: AsyncSession,
    payout_id: str,
    bot,
    admin_id: int,
) -> None:
    """
    Process one pending airtime payout:

      - Load the payout row in `airtime_payouts` with SELECT ... FOR UPDATE
      - Calls Flutterwave airtime API
      - Updates DB (status, tx ref, provider_response, completed_at)
      - Notifies user + admin via Telegram

    Safe to call from a periodic worker.
    """

    if AIRTIME_PROVIDER.lower() != "flutterwave":
        logger.warning(
            f"⚠️ Airtime provider '{AIRTIME_PROVIDER}' not supported in this service yet."
        )
        return

    # Lock the row FOR UPDATE to avoid double-processing
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
        # Already processed or not found
        logger.info(f"ℹ️ Airtime payout {payout_id} not found or not pending.")
        return

    # Access row data safely
    row_map = row._mapping  # SQLAlchemy RowMapping
    phone: str = row_map["phone_number"]
    amount: int = row_map["amount"]
    tg_id: int = row_map["tg_id"]

    logger.info(
        f"🚀 Processing airtime payout {payout_id} for TG {tg_id}, phone {phone}, amount ₦{amount}"
    )

    try:
        fw_data = await call_flutterwave_airtime(phone, amount)

        status: str = str(fw_data.get("status", "")).lower()
        # Try to get a stable reference from Flutterwave response
        data_block: Optional[Dict[str, Any]] = fw_data.get("data") or {}
        fw_ref: Optional[str] = (
            data_block.get("reference")
            or data_block.get("flw_ref")
            or data_block.get("tx_ref")
        )

        provider_json_str = json.dumps(fw_data)

        # SUCCESS PATH
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
                    "resp": provider_json_str,
                    "now": datetime.now(timezone.utc),
                },
            )

            # Mask phone: show only last 4 digits to user
            masked_phone = phone[:-4].rjust(len(phone), "•")

            # Notify user
            try:
                await bot.send_message(
                    tg_id,
                    (
                        "🎉 *Airtime Reward Credited!*\n\n"
                        f"✅ Amount: *₦{amount}*\n"
                        f"📱 Number: `{masked_phone}`\n\n"
                        "Keep playing and winning on *NaijaPrizeGate*! 🔥"
                    ),
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.error(f"❌ Failed to send airtime notification to user {tg_id}: {e}")

            # Notify admin
            try:
                await bot.send_message(
                    admin_id,
                    (
                        "📲 *Airtime AUTO-CREDITED via Flutterwave*\n\n"
                        f"👤 TG ID: `{tg_id}`\n"
                        f"📱 Phone: `{phone}`\n"
                        f"💸 Amount: *₦{amount}*\n"
                        f"🔗 FW Ref: `{fw_ref}`\n"
                    ),
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.error(f"❌ Failed to send airtime admin alert for payout {payout_id}: {e}")

            logger.info(f"✅ Airtime payout {payout_id} completed successfully.")
            return

        # FAILED PATH (but no exception thrown)
        await session.execute(
            text(
                """
                UPDATE airtime_payouts
                SET status = 'failed',
                    provider_response = :resp::jsonb
                WHERE id = :pid
                """
            ),
            {
                "pid": payout_id,
                "resp": provider_json_str,
            },
        )

        # Alert admin of failure so you can manually inspect/decide on re-try
        try:
            await bot.send_message(
                admin_id,
                (
                    "⚠️ *Airtime payout FAILED*\n\n"
                    f"Payout ID: `{payout_id}`\n"
                    f"TG ID: `{tg_id}`\n"
                    f"Phone: `{phone}`\n"
                    f"Amount: ₦{amount}\n"
                    f"Flutterwave status: `{status}`"
                ),
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error(f"❌ Failed to send admin failure alert for payout {payout_id}: {e}")

        logger.warning(
            f"⚠️ Airtime payout {payout_id} failed. Flutterwave status: {status}, data: {fw_data}"
        )

    except Exception as e:
        # Any unexpected exception path: mark as failed, send admin alert
        logger.error(f"❌ Exception while processing airtime payout {payout_id}: {e}")

        await session.execute(
            text(
                """
                UPDATE airtime_payouts
                SET status = 'failed'
                WHERE id = :pid
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
                    f"TG ID: `{tg_id}`\n"
                    f"Phone: `{phone}`\n"
                    f"Amount: ₦{amount}`\n"
                    f"Error: `{e}`"
                ),
                parse_mode="Markdown",
            )
        except Exception as e2:
            logger.error(
                f"❌ Failed to send admin EXCEPTION alert for payout {payout_id}: {e2}"
            )
