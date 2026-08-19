# ===============================================================
# services/finance/flutterwave_payout.py
# ===============================================================

import logging
from decimal import Decimal
from typing import Any
from uuid import UUID

from services.flutterwave_client import (
    create_bank_transfer,
    get_bank_transfer,
)

logger = logging.getLogger("finance.flutterwave_payout")


def build_withdrawal_reference(withdrawal_id: UUID) -> str:
    """
    Creates the merchant reference used for a Finance withdrawal.

    The UUID is converted to a compact, deterministic reference.
    """
    compact_id = str(withdrawal_id).replace("-", "").upper()
    return f"NPG-WD-{compact_id}"


def build_withdrawal_idempotency_key(withdrawal_id: UUID) -> str:
    """
    Creates a stable idempotency key for this withdrawal.

    The same withdrawal must always produce the same key.
    """
    return f"NPG-WD-IDEMP-{withdrawal_id}"


async def initiate_withdrawal_payout(
    *,
    withdrawal,
    account_bank: str,
    callback_url: str | None = None,
) -> dict[str, Any]:
    """
    Initiates the Flutterwave payout for a withdrawal.

    IMPORTANT:
    This function does not update the database.

    The caller remains responsible for:
    - changing withdrawal status;
    - storing payment_reference;
    - storing provider_reference;
    - committing the transaction.
    """

    withdrawal_id = withdrawal.id

    reference = (
        withdrawal.payment_reference
        or build_withdrawal_reference(withdrawal_id)
    )

    idempotency_key = build_withdrawal_idempotency_key(
        withdrawal_id
    )

    amount = int(
        Decimal(str(withdrawal.amount))
    )

    logger.info(
        "💸 Initiating withdrawal payout | "
        "withdrawal_id=%s | amount=%s | reference=%s",
        withdrawal_id,
        amount,
        reference,
    )

    result = await create_bank_transfer(
        account_bank=account_bank,
        account_number=withdrawal.account_number,
        amount=amount,
        beneficiary_name=withdrawal.account_name,
        reference=reference,
        narration=(
            f"NaijaPrizeGate referral withdrawal "
            f"{reference}"
        ),
        callback_url=callback_url,
        meta={
            "withdrawal_id": str(withdrawal_id),
            "source": "referral_wallet",
        },
    )

    result["payment_reference"] = reference
    result["idempotency_key"] = idempotency_key

    return result


async def get_withdrawal_payout_status(
    *,
    provider_reference: str,
) -> dict[str, Any]:
    """
    Retrieves the current Flutterwave transfer status.

    `provider_reference` is expected to be the Flutterwave
    transfer ID returned by the transfer API.
    """

    if not provider_reference:
        return {
            "success": False,
            "status": "error",
            "error": "missing provider_reference",
        }

    return await get_bank_transfer(
        transfer_id=provider_reference,
    )
