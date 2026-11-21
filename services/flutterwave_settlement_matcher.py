
# ============================================================
# services/flutterwave_settlement_matcher.py
# ============================================================

"""
Match synchronized Flutterwave collection transactions
against the six local payment sources.

Safety:
- Reads local payment records only.
- Updates only Flutterwave reconciliation records.
- Does not modify customer payments, balances, credits,
  commissions or fulfilment.
- Does not commit the caller's database transaction.
"""

from collections import Counter
from typing import Any

from sqlalchemy import select

from models import FlutterwaveCollectionTransaction

from services.flutterwave_payment_collector import (
    collect_local_payments,
)

from services.flutterwave_reconciliation import (
    index_local_payments,
    match_transaction,
)


PROVIDER = "FLUTTERWAVE"

MATCHED_STATUSES = {
    "MATCHED",
}


def _transaction_for_matching(record: Any) -> dict:
    """
    Translate ORM transaction fields into the format
    expected by match_transaction().
    """
    return {
        "id": record.flutterwave_transaction_id,
        "tx_ref": record.tx_ref,
        "amount": record.charged_amount,
        "currency": record.currency,
        "status": record.transaction_status,
    }


def _assign_match_result(
    record: Any,
    result: dict,
) -> str:
    """
    Apply the existing reconciliation engine's result
    without inventing new reconciliation statuses.
    """
    local_payment = result.get("local_payment")
    status = result["status"]

    if local_payment is not None:
        record.local_payment_source = (
            local_payment["local_payment_source"]
        )
        record.local_payment_id = (
            local_payment["local_payment_id"]
        )
    else:
        record.local_payment_source = None
        record.local_payment_id = None

    record.reconciliation_status = status
    record.is_local_payment_matched = (
        status == "MATCHED"
    )

    return status


def summarize_match_results(
    statuses: list[str],
) -> dict:
    """Calculate reconciliation counters from actual outcomes."""
    counts = Counter(statuses)

    total = len(statuses)
    matched = sum(
        counts[status]
        for status in MATCHED_STATUSES
    )

    unmatched = total - matched

    return {
        "total_transactions": total,
        "matched_transactions": matched,
        "unmatched_transactions": unmatched,
        "unresolved_discrepancies": unmatched,
        "status_counts": dict(sorted(counts.items())),
    }


async def match_flutterwave_collection_transactions(
    session,
) -> dict:
    """
    Match all synchronized Flutterwave collection transactions.

    The caller owns the SQLAlchemy transaction and must commit
    or roll it back.

    This function deliberately does not commit.
    """
    # 1. Read all six local payment sources.
    local_payments = await collect_local_payments(session)

    # 2. Index local records without discarding duplicates.
    local_payments_by_reference = index_local_payments(
        local_payments
    )

    # 3. Retrieve synchronized Flutterwave transactions.
    result = await session.execute(
        select(FlutterwaveCollectionTransaction).where(
            FlutterwaveCollectionTransaction.provider == PROVIDER
        )
    )

    flutterwave_transactions = result.scalars().all()

    # 4. Match and update only reconciliation records.
    statuses = []

    for record in flutterwave_transactions:
        transaction_data = _transaction_for_matching(record)

        match_result = match_transaction(
            transaction_data,
            local_payments_by_reference,
        )

        status = _assign_match_result(
            record,
            match_result,
        )

        statuses.append(status)

    # 5. Flush changes without committing the caller's transaction.
    await session.flush()

    summary = summarize_match_results(statuses)

    summary["local_payment_records_read"] = len(local_payments)

    return summary
