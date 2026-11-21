
# ============================================================
# services/flutterwave_settlement_sync.py
# ============================================================

"""
Flutterwave V3 settlement synchronisation.

Responsibilities:
- Retrieve settlement batches and their transaction details.
- Normalize API payloads.
- Upsert settlement and transaction records.
- Link transactions to their local settlement records.
- Track successful and failed synchronisation runs.

Safety:
- Does not modify customer payment tables.
- Does not update customer balances, credits or fulfilment.
- Uses PostgreSQL-native upserts.
- Preserves separate Flutterwave transaction IDs.
- Skips transactions without a Flutterwave transaction ID.
- Fetches API data before opening the database write transaction.
"""

import logging
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from models import (
    FlutterwaveSettlement,
    FlutterwaveCollectionTransaction,
    FlutterwaveReconciliationRun,
)

from services.flutterwave_settlement_client import (
    FlutterwaveSettlementClient,
)

from services.flutterwave_settlement_normalizer import (
    FlutterwavePayloadError,
    normalize_settlement_payload,
    normalize_transaction_payload,
)

from services.flutterwave_settlement_matcher import (
    match_flutterwave_collection_transactions,
)

logger = logging.getLogger("flutterwave_settlement_sync")


RUN_TYPE = "SETTLEMENT_SYNC"
PROVIDER = "FLUTTERWAVE"


def _parse_date(value: str, field_name: str) -> date:
    """Validate a YYYY-MM-DD date."""
    if not isinstance(value, str):
        raise ValueError(
            f"{field_name} must use YYYY-MM-DD format"
        )

    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError(
            f"{field_name} must use YYYY-MM-DD format"
        ) from None

    if parsed.isoformat() != value:
        raise ValueError(
            f"{field_name} must use YYYY-MM-DD format"
        )

    return parsed


def _period_datetime(value: date, *, end: bool = False) -> datetime:
    """Convert a calendar date to a UTC period boundary."""
    boundary = time.max if end else time.min

    return datetime.combine(
        value,
        boundary,
        tzinfo=timezone.utc,
    )


def _money_total(values: list[Decimal | None]) -> Decimal:
    """Sum available monetary values without using floats."""
    return sum(
        (value for value in values if value is not None),
        Decimal("0.00"),
    ).quantize(Decimal("0.01"))


def _prepare_settlement_payloads(
    settlement_summaries: list[dict],
    settlement_details: list[dict],
) -> list[dict]:
    """
    Combine settlement summaries with their detail responses.

    Detail values take precedence, while summary fields are
    retained when the detail response omits them.
    """
    prepared = []

    if len(settlement_summaries) != len(settlement_details):
        raise FlutterwavePayloadError(
            "Settlement summary/detail count mismatch"
        )

    for summary, detail in zip(
        settlement_summaries,
        settlement_details,
    ):
        if not isinstance(summary, dict):
            raise FlutterwavePayloadError(
                "Settlement summary must be an object"
            )

        if not isinstance(detail, dict):
            raise FlutterwavePayloadError(
                "Settlement detail must be an object"
            )

        summary_id = str(summary.get("id", "")).strip()
        detail_id = str(detail.get("id", "")).strip()

        if not summary_id or not detail_id:
            raise FlutterwavePayloadError(
                "Settlement ID is missing"
            )

        if summary_id != detail_id:
            raise FlutterwavePayloadError(
                "Settlement summary/detail ID mismatch"
            )

        combined = dict(summary)
        combined.update(detail)

        transactions = combined.get("transactions")

        if not isinstance(transactions, list):
            raise FlutterwavePayloadError(
                f"Settlement {summary_id} has no valid transactions list"
            )

        combined["transactions"] = transactions
        prepared.append(combined)

    return prepared


async def _fetch_settlement_payloads(
    client: FlutterwaveSettlementClient,
    from_date: str,
    to_date: str,
) -> list[dict]:
    """Fetch all settlement summaries and complete details."""
    summaries = await client.get_all_settlements(
        from_date=from_date,
        to_date=to_date,
    )

    details = []

    for summary in summaries:
        if not isinstance(summary, dict):
            raise FlutterwavePayloadError(
                "Flutterwave returned an invalid settlement summary"
            )

        settlement_id = summary.get("id")

        if settlement_id is None or not str(settlement_id).strip():
            raise FlutterwavePayloadError(
                "Flutterwave returned a settlement without an ID"
            )

        detail = await client.get_settlement(
            settlement_id=settlement_id,
        )

        details.append(detail)

    return _prepare_settlement_payloads(
        summaries,
        details,
    )


def _normalize_fetched_payloads(
    settlements: list[dict],
) -> dict:
    """Normalize all settlement and transaction payloads."""
    normalized_settlements = []
    normalized_transactions = []
    skipped_missing_transaction_ids = []

    seen_transaction_ids = set()

    for settlement_payload in settlements:
        settlement = normalize_settlement_payload(
            settlement_payload
        )

        settlement_transactions = []

        for transaction_payload in settlement_payload["transactions"]:
            if not isinstance(transaction_payload, dict):
                raise FlutterwavePayloadError(
                    "Settlement contains an invalid transaction object"
                )

            if not str(
                transaction_payload.get("tx_ref") or ""
            ).strip():
                skipped_missing_transaction_ids.append({
                    "settlement_id": (
                        settlement["flutterwave_settlement_id"]
                    ),
                    "tx_ref": None,
                    "reason": "MISSING_FLUTTERWAVE_REFERENCE",
                })
                continue

            transaction = normalize_transaction_payload(
                transaction_payload
            )

            transaction_id = (
                transaction["flutterwave_transaction_id"]
            )

            if not transaction_id:
                skipped_missing_transaction_ids.append({
                    "settlement_id": (
                        settlement["flutterwave_settlement_id"]
                    ),
                    "tx_ref": transaction["tx_ref"],
                    "reason": "MISSING_FLUTTERWAVE_TRANSACTION_ID",
                })
                continue

            if transaction_id in seen_transaction_ids:
                # The same gateway transaction may appear more
                # than once in retrieved data. Keep one copy.
                continue

            seen_transaction_ids.add(transaction_id)

            settlement_transactions.append(transaction)

        normalized_settlements.append({
            "settlement": settlement,
            "transactions": settlement_transactions,
        })

        normalized_transactions.extend(
            settlement_transactions
        )

    return {
        "settlements": normalized_settlements,
        "transactions": normalized_transactions,
        "skipped_missing_transaction_ids": (
            skipped_missing_transaction_ids
        ),
    }


def _settlement_upsert_statement(values: dict):
    """Build a PostgreSQL settlement upsert."""
    insert_statement = pg_insert(
        FlutterwaveSettlement
    ).values(**values)

    update_fields = {
        key: getattr(insert_statement.excluded, key)
        for key in values
        if key not in {
            "provider",
            "flutterwave_settlement_id",
            "raw_payload",
        }
    }

    update_fields["raw_payload"] = (
        insert_statement.excluded.raw_payload
    )
    update_fields["last_synced_at"] = func.now()

    return insert_statement.on_conflict_do_update(
        constraint="uq_flutterwave_settlement",
        set_=update_fields,
    ).returning(
        FlutterwaveSettlement.id
    )


TRANSACTION_UPSERT_PROTECTED_FIELDS = {
    "provider",
    "flutterwave_transaction_id",
    "local_payment_source",
    "local_payment_id",
    "is_local_payment_matched",
    "reconciliation_status",
    "raw_payload",
}


def _transaction_upsert_statement(values: dict):
    """
    Build a PostgreSQL transaction upsert.

    The partial unique index is used because transactions
    without gateway IDs are not eligible for upsert.
    """
    insert_statement = pg_insert(
        FlutterwaveCollectionTransaction
    ).values(**values)

    update_fields = {
        key: getattr(insert_statement.excluded, key)
        for key in values
        if key not in TRANSACTION_UPSERT_PROTECTED_FIELDS
    }

    # Preserve any existing local reconciliation assignment.
    # Stage 3 will manage these fields.
    update_fields["raw_payload"] = (
        insert_statement.excluded.raw_payload
    )
    update_fields["last_synced_at"] = func.now()

    return insert_statement.on_conflict_do_update(
        index_elements=[
            FlutterwaveCollectionTransaction.provider,
            FlutterwaveCollectionTransaction.flutterwave_transaction_id,
        ],
        index_where=text(
            "flutterwave_transaction_id IS NOT NULL"
        ),
        set_=update_fields,
    )


async def _upsert_settlement(
    session,
    settlement_values: dict,
) -> Any:
    """Insert or update one settlement and return its local UUID."""
    result = await session.execute(
        _settlement_upsert_statement(settlement_values)
    )

    settlement_id = result.scalar_one()

    if settlement_id is None:
        raise RuntimeError(
            "Settlement upsert did not return a local UUID"
        )

    return settlement_id


async def _upsert_transaction(
    session,
    transaction_values: dict,
) -> None:
    """Insert or update one Flutterwave transaction."""
    await session.execute(
        _transaction_upsert_statement(transaction_values)
    )


async def _record_failed_run(
    session_factory: Callable,
    *,
    from_date: date,
    to_date: date,
    error: Exception,
) -> None:
    """
    Record a failed run in a separate transaction.

    This is called only after the main database transaction
    has failed or rolled back.
    """
    error_message = (
        f"{type(error).__name__}: {error}"
    )[:4000]

    try:
        async with session_factory() as session:
            async with session.begin():
                failed_run = FlutterwaveReconciliationRun(
                    run_type=RUN_TYPE,
                    run_status="FAILED",
                    reporting_date=to_date,
                    period_start=_period_datetime(from_date),
                    period_end=_period_datetime(
                        to_date,
                        end=True,
                    ),
                    currency="NGN",
                    total_transactions=0,
                    matched_transactions=0,
                    unmatched_transactions=0,
                    unresolved_discrepancies=0,
                    error_message=error_message,
                    summary={
                        "from_date": from_date.isoformat(),
                        "to_date": to_date.isoformat(),
                    },
                    completed_at=datetime.now(timezone.utc),
                )

                session.add(failed_run)

    except Exception:
        logger.exception(
            "Unable to persist failed Flutterwave synchronisation run"
        )


async def sync_flutterwave_settlements(
    from_date: str,
    to_date: str,
    *,
    client: FlutterwaveSettlementClient | None = None,
    session_factory: Callable | None = None,
) -> dict:
    """
    Synchronise Flutterwave settlements for a date range.

    Parameters:
        from_date:
            Inclusive YYYY-MM-DD settlement API filter.

        to_date:
            Inclusive YYYY-MM-DD settlement API filter.

        client:
            Optional injected Flutterwave client for testing.

        session_factory:
            Optional injected async SQLAlchemy session factory.

    Returns:
        A summary dictionary describing the synchronisation.

    The function does not modify customer payment tables.
    """
    from_date_value = _parse_date(
        from_date,
        "from_date",
    )
    to_date_value = _parse_date(
        to_date,
        "to_date",
    )

    if from_date_value > to_date_value:
        raise ValueError(
            "from_date cannot be later than to_date"
        )

    if session_factory is None:
        from db import AsyncSessionLocal

        session_factory = AsyncSessionLocal

    if client is None:
        client = FlutterwaveSettlementClient()

    # --------------------------------------------------------
    # 1. Fetch all API data before opening a DB transaction.
    # --------------------------------------------------------
    try:
        fetched_settlements = await _fetch_settlement_payloads(
            client,
            from_date,
            to_date,
        )

        normalized = _normalize_fetched_payloads(
            fetched_settlements
        )

    except Exception as exc:
        await _record_failed_run(
            session_factory,
            from_date=from_date_value,
            to_date=to_date_value,
            error=exc,
        )
        raise

    settlement_records = normalized["settlements"]
    skipped_records = normalized[
        "skipped_missing_transaction_ids"
    ]

    all_settlement_values = [
        item["settlement"]
        for item in settlement_records
    ]

    gross_total = _money_total([
        item.get("gross_amount")
        for item in all_settlement_values
    ])

    confirmed_net_total = _money_total([
        item.get("net_amount")
        for item in all_settlement_values
    ])

    settlement_count = len(settlement_records)
    transaction_count = len(normalized["transactions"])

    # --------------------------------------------------------
    # 2. Persist settlements, transactions and run atomically.
    # --------------------------------------------------------
    try:
        async with session_factory() as session:
            async with session.begin():

                for item in settlement_records:
                    settlement_values = dict(
                        item["settlement"]
                    )

                    settlement_transactions = (
                        item["transactions"]
                    )

                    local_settlement_id = (
                        await _upsert_settlement(
                            session,
                            settlement_values,
                        )
                    )

                    for transaction in settlement_transactions:
                        transaction_values = dict(
                            transaction
                        )

                        transaction_values["settlement_id"] = (
                            local_settlement_id
                        )

                        await _upsert_transaction(
                            session,
                            transaction_values,
                        )

                # ------------------------------------------------
                # Stage 3: Match all persisted Flutterwave
                # transactions against the six local payment
                # sources, within this same DB transaction.
                # ------------------------------------------------
                matching_summary = (
                    await match_flutterwave_collection_transactions(
                        session
                    )
                )

                completed_at = datetime.now(timezone.utc)

                run_summary = {
                    "from_date": from_date,
                    "to_date": to_date,

                    # Current synchronization period
                    "settlements_processed": settlement_count,
                    "transactions_processed": transaction_count,

                    # All persisted Flutterwave transactions
                    # evaluated by the matcher
                    "transactions_reconciled": (
                        matching_summary["total_transactions"]
                    ),

                    "local_payment_records_read": (
                        matching_summary[
                            "local_payment_records_read"
                        ]
                    ),

                    "matched_transactions": (
                        matching_summary["matched_transactions"]
                    ),
                    "unmatched_transactions": (
                        matching_summary["unmatched_transactions"]
                    ),
                    "unresolved_discrepancies": (
                        matching_summary["unresolved_discrepancies"]
                        + len(skipped_records)
                    ),

                    "reconciliation_status_counts": (
                        matching_summary["status_counts"]
                    ),

                    "transactions_skipped_invalid_identity": len(
                        skipped_records
                    ),
                    "skipped_transactions": skipped_records,

                    "gross_collections": str(gross_total),
                    "confirmed_net_amount": str(
                        confirmed_net_total
                    ),

                    "local_payment_matching": "COMPLETED",

                    # Admin notification delivery tracking
                    "admin_notification_status": "PENDING",
                    "admin_notification_delivered_to": [],
                    "admin_notification_failed_for": [],
                }

                run = FlutterwaveReconciliationRun(
                    run_type=RUN_TYPE,
                    run_status="COMPLETED",
                    reporting_date=to_date_value,
                    period_start=_period_datetime(
                        from_date_value
                    ),
                    period_end=_period_datetime(
                        to_date_value,
                        end=True,
                    ),
                    currency="NGN",
                    total_transactions=(
                        matching_summary["total_transactions"]
                    ),
                    matched_transactions=(
                        matching_summary["matched_transactions"]
                    ),
                    unmatched_transactions=(
                        matching_summary["unmatched_transactions"]
                    ),
                    unresolved_discrepancies=(
                        matching_summary["unresolved_discrepancies"]
                        + len(skipped_records)
                    ),
                    gross_collections=gross_total,
                    confirmed_net_amount=confirmed_net_total,
                    summary=run_summary,
                    completed_at=completed_at,
                )

                session.add(run)
                await session.flush()

        logger.info(
            "Flutterwave settlement sync completed: "
            "%s settlements, %s transactions, %s skipped",
            settlement_count,
            transaction_count,
            len(skipped_records),
        )

        return {
            "run_status": "COMPLETED",
            "settlements_processed": settlement_count,

            # Transactions retrieved during this sync period
            "transactions_processed": transaction_count,

            # All persisted Flutterwave transactions re-evaluated
            "transactions_reconciled": (
                matching_summary["total_transactions"]
            ),

            "transactions_skipped_invalid_identity": len(
                skipped_records
            ),

            "gross_collections": gross_total,
            "confirmed_net_amount": confirmed_net_total,

            "matched_transactions": (
                matching_summary["matched_transactions"]
            ),
            "unmatched_transactions": (
                matching_summary["unmatched_transactions"]
            ),
            "unresolved_discrepancies": (
                matching_summary["unresolved_discrepancies"]
                + len(skipped_records)
            ),

            "reconciliation_status_counts": (
                matching_summary["status_counts"]
            ),

            "local_matching_performed": True,
            "skipped_transactions": skipped_records,
        }

    except Exception as exc:
        logger.exception(
            "Flutterwave settlement synchronisation failed"
        )

        await _record_failed_run(
            session_factory,
            from_date=from_date_value,
            to_date=to_date_value,
            error=exc,
        )

        raise
