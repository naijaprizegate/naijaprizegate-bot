# ==========================================
# services/flutterwave_payment_collector.py
# ==========================================


from decimal import Decimal
from typing import Any

from sqlalchemy import text


ALL_LOCAL_PAYMENTS_SQL = text("""
    SELECT
        'payments' AS local_payment_source,
        id::text AS local_payment_id,
        tx_ref,
        amount AS amount,
        currency::text AS currency,
        'DATABASE' AS currency_source,
        payment_provider AS provider,
        status AS local_status,
        created_at
    FROM payments

    UNION ALL

    SELECT
        'jamb_payments',
        id::text,
        payment_reference,
        amount_paid,
        'NGN',
        'CHECKOUT_CONFIGURATION',
        provider,
        payment_status,
        created_at
    FROM jamb_payments

    UNION ALL

    SELECT
        'mockjamb_payments',
        id::text,
        payment_reference,
        amount_paid,
        'NGN',
        'CHECKOUT_CONFIGURATION',
        NULL::text,
        payment_status,
        created_at
    FROM mockjamb_payments

    UNION ALL

    SELECT
        'mockwaec_payments',
        id::text,
        payment_reference,
        amount_paid,
        'NGN',
        'CHECKOUT_CONFIGURATION',
        NULL::text,
        payment_status,
        created_at
    FROM mockwaec_payments

    UNION ALL

    SELECT
        'waec_payments',
        id::text,
        payment_reference,
        amount_paid,
        'NGN',
        'CHECKOUT_CONFIGURATION',
        NULL::text,
        payment_status,
        created_at
    FROM waec_payments

    UNION ALL

    SELECT
        'university_payments',
        id::text,
        payment_reference,
        amount_paid,
        'NGN',
        'CHECKOUT_CONFIGURATION',
        provider,
        payment_status,
        created_at
    FROM university_payments

    ORDER BY created_at ASC
""")


def _normalize_reference(value: Any) -> str | None:
    if value is None:
        return None

    reference = str(value).strip()
    return reference or None


def _normalize_provider(value: Any) -> str | None:
    if value is None:
        return None

    provider = str(value).strip().lower()

    if provider in {"flw", "flutterwave"}:
        return "flutterwave"

    return provider or None


def _normalize_amount(value: Any) -> Decimal:
    if value is None:
        raise ValueError("Local payment has no amount")

    amount = Decimal(str(value))

    if not amount.is_finite():
        raise ValueError("Payment amount must be finite")

    return amount.quantize(Decimal("0.01"))


def normalize_local_payment(row: dict) -> dict:
    """
    Normalize one local payment record.

    Provider attribution is preserved. A missing provider is
    never assumed to mean Flutterwave.
    """
    source = row["local_payment_source"]
    provider = _normalize_provider(row.get("provider"))
    reference = _normalize_reference(row.get("tx_ref"))

    if provider == "flutterwave":
        attribution = "CONFIRMED_PROVIDER_CONFIGURATION"
        eligibility = "CANDIDATE_FOR_API_VERIFICATION"

    elif provider is None:
        attribution = "PROVIDER_NOT_RECORDED"
        eligibility = "UNVERIFIED_PROVIDER"

    else:
        attribution = "OTHER_PROVIDER"
        eligibility = "EXCLUDED_NON_FLUTTERWAVE"

    if eligibility == "EXCLUDED_NON_FLUTTERWAVE":
        reconciliation_status = "EXCLUDED_NON_FLUTTERWAVE"

    elif not reference:
        reconciliation_status = "UNRESOLVED_MISSING_REFERENCE"

    elif eligibility == "UNVERIFIED_PROVIDER":
        reconciliation_status = "UNRESOLVED_PROVIDER_ATTRIBUTION"

    else:
        reconciliation_status = "PENDING_FLUTTERWAVE_VERIFICATION"

    return {
        "local_payment_source": source,
        "local_payment_id": str(row["local_payment_id"]),
        "tx_ref": reference,
        "amount": _normalize_amount(row.get("amount")),
        "currency": str(row.get("currency") or "").strip().upper() or None,
        "currency_source": row.get("currency_source"),
        "provider": provider,
        "provider_attribution": attribution,
        "collection_eligibility": eligibility,
        "local_status": str(row.get("local_status") or "").strip().lower(),
        "created_at": row.get("created_at"),
        "reconciliation_status": reconciliation_status,
    }


async def collect_local_payments(session) -> list[dict]:
    """
    Read local payment records from all six payment sources.

    This function performs SELECT operations only.
    It does not update payment records, credits, balances or
    fulfilment statuses.
    """
    result = await session.execute(ALL_LOCAL_PAYMENTS_SQL)

    records = []

    for row in result.mappings().all():
        records.append(normalize_local_payment(dict(row)))

    return records


async def collect_local_successful_payments(session) -> list[dict]:
    """
    Backward-compatible alias.

    Despite the historical function name, this now collects
    all local payment statuses.
    """
    return await collect_local_payments(session)

def summarize_collected_payments(records: list[dict]) -> dict:
    """Summarize records without treating unresolved payments as verified."""

    candidate_count = 0
    candidate_amount = Decimal("0.00")

    excluded_count = 0
    excluded_amount = Decimal("0.00")

    unresolved_reference_count = 0
    unresolved_provider_count = 0

    for record in records:
        eligibility = record["collection_eligibility"]
        status = record["reconciliation_status"]
        amount = record["amount"]

        if eligibility == "EXCLUDED_NON_FLUTTERWAVE":
            excluded_count += 1
            excluded_amount += amount
            continue

        if status == "UNRESOLVED_MISSING_REFERENCE":
            unresolved_reference_count += 1
            continue

        if eligibility == "UNVERIFIED_PROVIDER":
            unresolved_provider_count += 1
            continue

        candidate_count += 1
        candidate_amount += amount

    return {
        "total_records_read": len(records),
        "candidate_count": candidate_count,
        "candidate_amount": candidate_amount,
        "excluded_count": excluded_count,
        "excluded_amount": excluded_amount,
        "unresolved_reference_count": unresolved_reference_count,
        "unresolved_provider_count": unresolved_provider_count,
    }