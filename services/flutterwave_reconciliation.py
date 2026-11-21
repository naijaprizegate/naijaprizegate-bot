# ==========================================
# services/flutterwave_reconciliation.py
# ==========================================


from decimal import Decimal, InvalidOperation
from typing import Any, Iterable


class ReconciliationError(Exception):
    """Raised when reconciliation data is invalid."""


def normalize_reference(value: Any) -> str | None:
    """Normalize a transaction reference without changing its identity."""
    if value is None:
        return None

    reference = str(value).strip()
    return reference or None


def normalize_currency(value: Any) -> str | None:
    """Normalize a currency code."""
    if value is None:
        return None

    currency = str(value).strip().upper()
    return currency or None


def to_decimal(value: Any) -> Decimal:
    """Convert a monetary value safely to Decimal."""
    if value is None or isinstance(value, bool):
        raise ReconciliationError("Monetary value is missing or invalid")

    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ReconciliationError(
            f"Invalid monetary value: {value!r}"
        ) from None

    if not amount.is_finite():
        raise ReconciliationError("Monetary value must be finite")

    return amount.quantize(Decimal("0.01"))


def index_local_payments(
    local_payments: Iterable[dict],
) -> dict[str, list[dict]]:
    """Group local payment records by transaction reference."""
    indexed = {}

    for payment in local_payments:
        reference = normalize_reference(payment.get("tx_ref"))

        if not reference:
            continue

        indexed.setdefault(reference, []).append(payment)

    return indexed



def match_transaction(
    flutterwave_transaction: dict,
    local_payments_by_reference: dict[str, list[dict]],
) -> dict:
    """
    Match one Flutterwave transaction to a local payment.

    A successful match requires:
    - A valid transaction reference.
    - Exactly one corresponding local payment.
    - A successful Flutterwave transaction.
    - Matching amount and currency.
    - No explicit conflicting local provider.

    Local payment status is reported separately so that a
    successful Flutterwave transaction is not mistaken for
    a successfully updated local payment.
    """
    reference = normalize_reference(
        flutterwave_transaction.get("tx_ref")
    )

    if not reference:
        return {
            "status": "UNMATCHED",
            "reason": "MISSING_FLUTTERWAVE_REFERENCE",
            "local_payment": None,
        }

    candidates = local_payments_by_reference.get(reference, [])

    if not candidates:
        return {
            "status": "NO_LOCAL_PAYMENT",
            "reason": "NO_LOCAL_PAYMENT",
            "local_payment": None,
        }

    if len(candidates) > 1:
        return {
            "status": "AMBIGUOUS",
            "reason": "DUPLICATE_LOCAL_REFERENCE",
            "local_payment": None,
        }

    local_payment = candidates[0]

    flw_status = str(
        flutterwave_transaction.get("status") or ""
    ).strip().lower()

    if flw_status not in {"successful", "success", "completed"}:
        return {
            "status": "UNMATCHED",
            "reason": "FLUTTERWAVE_TRANSACTION_NOT_SUCCESSFUL",
            "local_payment": local_payment,
        }

    local_provider = str(
        local_payment.get("provider") or ""
    ).strip().lower()

    if local_provider == "flw":
        local_provider = "flutterwave"

    if local_provider and local_provider != "flutterwave":
        return {
            "status": "PROVIDER_CONFLICT",
            "reason": "LOCAL_PAYMENT_PROVIDER_CONFLICT",
            "local_payment": local_payment,
        }

    try:
        flw_amount = to_decimal(
            flutterwave_transaction.get("amount")
        )
        local_amount = to_decimal(
            local_payment.get("amount")
        )
    except ReconciliationError:
        return {
            "status": "MISMATCH",
            "reason": "INVALID_AMOUNT",
            "local_payment": local_payment,
        }

    if flw_amount != local_amount:
        return {
            "status": "MISMATCH",
            "reason": "AMOUNT_MISMATCH",
            "local_payment": local_payment,
        }

    flw_currency = normalize_currency(
        flutterwave_transaction.get("currency")
    )
    local_currency = normalize_currency(
        local_payment.get("currency")
    )

    if not flw_currency or not local_currency:
        return {
            "status": "MISMATCH",
            "reason": "MISSING_CURRENCY",
            "local_payment": local_payment,
        }

    if flw_currency != local_currency:
        return {
            "status": "MISMATCH",
            "reason": "CURRENCY_MISMATCH",
            "local_payment": local_payment,
        }

    local_status = str(
        local_payment.get("local_status") or ""
    ).strip().lower()

    if local_status == "successful":
        status = "MATCHED"
        reason = None

    elif local_status == "pending":
        status = "FLUTTERWAVE_SUCCESS_LOCAL_PENDING"
        reason = "LOCAL_PAYMENT_STILL_PENDING"

    elif local_status in {"failed", "expired", "cancelled", "canceled"}:
        status = "FLUTTERWAVE_SUCCESS_LOCAL_FAILED"
        reason = "LOCAL_PAYMENT_NOT_SUCCESSFUL"

    else:
        status = "FLUTTERWAVE_SUCCESS_LOCAL_STATUS_UNKNOWN"
        reason = "LOCAL_PAYMENT_STATUS_UNKNOWN"

    return {
        "status": status,
        "reason": reason,
        "local_payment": local_payment,
    }


def calculate_settlement_totals(
    settlements: Iterable[dict],
) -> dict:
    """
    Calculate totals from Flutterwave settlement records.

    Actual net amounts are kept separate from estimated amounts.
    No fee percentage is assumed.
    """
    gross_total = Decimal("0.00")
    confirmed_net_total = Decimal("0.00")
    settlement_count = 0
    confirmed_net_count = 0
    missing_net_count = 0

    for settlement in settlements:
        settlement_count += 1

        gross_value = settlement.get("gross_amount")
        net_value = settlement.get("net_amount")

        if gross_value is not None:
            gross_total += to_decimal(gross_value)

        if net_value is not None:
            confirmed_net_total += to_decimal(net_value)
            confirmed_net_count += 1
        else:
            missing_net_count += 1

    return {
        "settlement_count": settlement_count,
        "gross_total": gross_total,
        "confirmed_net_total": confirmed_net_total,
        "confirmed_net_count": confirmed_net_count,
        "missing_net_count": missing_net_count,
    }


def separate_duplicate_flutterwave_transactions(
    transactions: Iterable[dict],
) -> dict:
    """
    Separate unique Flutterwave transactions from duplicate records.

    Transaction ID is the primary duplicate key.
    A repeated non-empty tx_ref with different transaction IDs
    is flagged as a reference conflict, not silently discarded.
    """
    unique_transactions = []
    seen_ids = {}
    seen_references = {}
    duplicate_ids = []
    reference_conflicts = []

    for transaction in transactions:
        transaction_id = transaction.get("id")
        reference = normalize_reference(
            transaction.get("tx_ref")
        )

        if transaction_id is not None:
            transaction_id = str(transaction_id).strip()

            if transaction_id in seen_ids:
                duplicate_ids.append({
                    "transaction_id": transaction_id,
                    "tx_ref": reference,
                })
                continue

        if reference and reference in seen_references:
            previous_id = seen_references[reference]

            if transaction_id != previous_id:
                reference_conflicts.append({
                    "tx_ref": reference,
                    "first_transaction_id": previous_id,
                    "second_transaction_id": transaction_id,
                })

        if transaction_id is not None:
            seen_ids[transaction_id] = transaction
        if reference:
            seen_references[reference] = transaction_id

        unique_transactions.append(transaction)

    return {
        "transactions": unique_transactions,
        "duplicate_ids": duplicate_ids,
        "reference_conflicts": reference_conflicts,
    }
