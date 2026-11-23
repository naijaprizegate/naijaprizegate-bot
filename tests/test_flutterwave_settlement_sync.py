
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from models import (
    FlutterwaveSettlement,
    FlutterwaveCollectionTransaction,
)

from services.flutterwave_settlement_sync import (
    FlutterwavePayloadError,
    _fetch_transaction_payloads,
    _normalize_fetched_payloads,
    _settlement_upsert_statement,
    _transaction_upsert_statement,
    sync_flutterwave_settlements,
)

SETTLEMENT_ID = "6912585"
LOCAL_SETTLEMENT_UUID = uuid4()

MATCHING_SUMMARY = {
    "total_transactions": 1,
    "matched_transactions": 1,
    "unmatched_transactions": 0,
    "unresolved_discrepancies": 0,
    "status_counts": {
        "MATCHED": 1,
    },
    "local_payment_records_read": 1,
}


def settlement_payload():
    return {
        "id": SETTLEMENT_ID,
        "currency": "NGN",
        "transaction_date": "2026-08-13T00:00:00Z",
        "due_date": "2026-08-14T00:00:00Z",
        "processed_date": "2026-08-14T08:00:00Z",
        "status": "completed",
        "gross_amount": "200.00",
        "app_fee": "4.00",
        "merchant_fee": "0.00",
        "stampduty_charge": "0.00",
        "chargeback": "0.00",
        "refund": "0.00",
        "net_amount": "195.69",
        "transaction_count": 1,
        "destination": {"type": "bank"},
        "transactions": [],
    }


def transaction_payload():
    return {
        "id": "2079349690",
        "tx_ref": "TRIVIA-223059B970C2",
        "charged_amount": "200.00",
        "settlement_amount": "195.69",
        "currency": "NGN",
        "status": "successful",
        "payment_entity": "card",
        "customer_email": "customer@example.com",
        "transaction_date": "2026-08-13T09:52:10Z",
        "app_fee": "4.00",
        "merchant_fee": "0.00",
        "stampduty_charge": "0.00",
    }


class FakeResult:
    def scalar_one(self):
        return LOCAL_SETTLEMENT_UUID


class FakeTransactionContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeSession:
    def __init__(self, *, fail_execute=False):
        self.statements = []
        self.added = []
        self.fail_execute = fail_execute
        self.flush = AsyncMock()

    def begin(self):
        return FakeTransactionContext()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, statement):
        self.statements.append(statement)

        if self.fail_execute:
            raise RuntimeError("Simulated database failure")

        return FakeResult()

    def add(self, item):
        self.added.append(item)



class FakeClient:
    def __init__(
        self,
        summaries=None,
        details=None,
        transactions=None,
        error=None,
    ):
        self.summaries = summaries or []
        self.details = details or []
        self.transactions = transactions or []
        self.error = error

        self.get_all_settlements = AsyncMock(
            side_effect=self._get_summaries
        )
        self.get_settlement = AsyncMock(
            side_effect=self._get_detail
        )
        self.get_all_transactions = AsyncMock(
            side_effect=self._get_transactions
        )

    async def _get_summaries(self, **kwargs):
        if self.error:
            raise self.error
        return self.summaries

    async def _get_detail(self, settlement_id):
        if self.error:
            raise self.error

        return self.details.pop(0)

    async def _get_transactions(self, **kwargs):
        if self.error:
            raise self.error
        return self.transactions


def make_session_factory(sessions):
    """Return a factory that supplies sessions in order."""
    @asynccontextmanager
    async def factory():
        session = sessions.pop(0)
        yield session

    return factory


@pytest.mark.asyncio
async def test_normalized_fetched_payloads():
    settlement = settlement_payload()
    settlement["transactions"] = [transaction_payload()]

    result = _normalize_fetched_payloads([settlement])

    assert len(result["settlements"]) == 1
    assert len(result["transactions"]) == 1

    transaction = result["transactions"][0]

    assert transaction["tx_ref"] == "TRIVIA-223059B970C2"
    assert transaction["charged_amount"] == Decimal("200.00")
    assert result["skipped_missing_transaction_ids"] == []


@pytest.mark.asyncio
async def test_missing_transaction_id_is_skipped():
    settlement = settlement_payload()

    transaction = transaction_payload()
    transaction.pop("id")
    settlement["transactions"] = [transaction]

    result = _normalize_fetched_payloads([settlement])

    assert result["transactions"] == []
    assert len(result["skipped_missing_transaction_ids"]) == 1


@pytest.mark.asyncio
async def test_missing_transaction_reference_is_skipped():
    settlement = settlement_payload()

    transaction = transaction_payload()
    transaction.pop("tx_ref")
    settlement["transactions"] = [transaction]

    result = _normalize_fetched_payloads([settlement])

    assert result["transactions"] == []
    assert (
        result["skipped_missing_transaction_ids"][0]["reason"]
        == "MISSING_FLUTTERWAVE_REFERENCE"
    )


def test_settlement_upsert_uses_unique_constraint():
    values = {
        "provider": "FLUTTERWAVE",
        "flutterwave_settlement_id": SETTLEMENT_ID,
        "currency": "NGN",
        "raw_payload": {},
    }

    statement = _settlement_upsert_statement(values)
    compiled = str(
        statement.compile(dialect=postgresql.dialect())
    )

    assert "ON CONFLICT ON CONSTRAINT uq_flutterwave_settlement" in compiled
    assert "RETURNING flutterwave_settlements.id" in compiled


def test_transaction_upsert_uses_partial_unique_index():
    values = {
        "provider": "FLUTTERWAVE",
        "flutterwave_transaction_id": "2079349690",
        "tx_ref": "TRIVIA-223059B970C2",
        "currency": "NGN",
        "local_payment_source": None,
        "local_payment_id": None,
        "settlement_id": LOCAL_SETTLEMENT_UUID,
        "is_local_payment_matched": False,
        "reconciliation_status": "UNMATCHED",
        "raw_payload": {},
    }

    statement = _transaction_upsert_statement(values)
    compiled = str(
        statement.compile(dialect=postgresql.dialect())
    )

    assert "ON CONFLICT" in compiled
    assert "flutterwave_transaction_id IS NOT NULL" in compiled


@pytest.mark.asyncio
async def test_successful_sync_persists_settlement_transaction_and_run():
    settlement = settlement_payload()
    settlement["transactions"] = [transaction_payload()]

    detail = dict(settlement)

    client = FakeClient(
        summaries=[settlement],
        details=[detail],
        transactions=[transaction_payload()],
    )

    session = FakeSession()
    factory = make_session_factory([session])

    with patch(
        "services.flutterwave_settlement_sync."
        "match_flutterwave_collection_transactions",
        new=AsyncMock(return_value=MATCHING_SUMMARY),
    ) as mock_matcher:

        result = await sync_flutterwave_settlements(
            "2026-08-13",
            "2026-08-13",
            client=client,
            session_factory=factory,
        )

    mock_matcher.assert_awaited_once_with(session)

    assert result["run_status"] == "COMPLETED"
    assert result["settlements_processed"] == 1
    assert result["transactions_processed"] == 1
    assert result["transactions_reconciled"] == 1
    assert result["confirmed_net_amount"] == Decimal("195.69")
    assert result["gross_collections"] == Decimal("200.00")
    assert result["local_matching_performed"] is True
    assert result["matched_transactions"] == 1
    assert result["unmatched_transactions"] == 0

    assert len(session.statements) == 2
    assert len(session.added) == 1

    run = session.added[0]

    assert run.run_type == "SETTLEMENT_SYNC"
    assert run.run_status == "COMPLETED"
    assert run.total_transactions == 1
    assert run.matched_transactions == 1
    assert run.unmatched_transactions == 0
    assert run.confirmed_net_amount == Decimal("195.69")


@pytest.mark.asyncio
async def test_empty_settlement_list_completes_successfully():
    client = FakeClient()

    session = FakeSession()
    factory = make_session_factory([session])

    empty_matching_summary = {
        "total_transactions": 0,
        "matched_transactions": 0,
        "unmatched_transactions": 0,
        "unresolved_discrepancies": 0,
        "status_counts": {},
        "local_payment_records_read": 0,
    }

    with patch(
        "services.flutterwave_settlement_sync."
        "match_flutterwave_collection_transactions",
        new=AsyncMock(return_value=empty_matching_summary),
    ) as mock_matcher:

        result = await sync_flutterwave_settlements(
            "2026-08-13",
            "2026-08-13",
            client=client,
            session_factory=factory,
        )

    mock_matcher.assert_awaited_once_with(session)

    assert result["run_status"] == "COMPLETED"
    assert result["settlements_processed"] == 0
    assert result["transactions_processed"] == 0
    assert result["transactions_reconciled"] == 0
    assert result["local_matching_performed"] is True

    assert len(session.statements) == 0
    assert len(session.added) == 1
    assert session.added[0].run_status == "COMPLETED"


@pytest.mark.asyncio
async def test_api_failure_records_failed_run():
    client = FakeClient(
        error=RuntimeError("Simulated API failure")
    )

    failed_session = FakeSession()
    factory = make_session_factory([failed_session])

    with pytest.raises(RuntimeError, match="Simulated API failure"):
        await sync_flutterwave_settlements(
            "2026-08-13",
            "2026-08-13",
            client=client,
            session_factory=factory,
        )

    assert len(failed_session.added) == 1

    failed_run = failed_session.added[0]

    assert failed_run.run_type == "SETTLEMENT_SYNC"
    assert failed_run.run_status == "FAILED"
    assert "Simulated API failure" in failed_run.error_message


@pytest.mark.asyncio
async def test_database_failure_records_failed_run_separately():
    settlement = settlement_payload()
    settlement["transactions"] = [transaction_payload()]

    client = FakeClient(
        summaries=[settlement],
        details=[dict(settlement)],
    )

    main_session = FakeSession(fail_execute=True)
    failure_session = FakeSession()

    factory = make_session_factory([
        main_session,
        failure_session,
    ])

    with pytest.raises(RuntimeError, match="Simulated database failure"):
        await sync_flutterwave_settlements(
            "2026-08-13",
            "2026-08-13",
            client=client,
            session_factory=factory,
        )

    assert len(failure_session.added) == 1
    assert failure_session.added[0].run_status == "FAILED"


@pytest.mark.asyncio
async def test_invalid_date_range_is_rejected():
    with pytest.raises(ValueError, match="cannot be later"):
        await sync_flutterwave_settlements(
            "2026-08-14",
            "2026-08-13",
            client=FakeClient(),
            session_factory=lambda: None,
        )

def test_transaction_upsert_preserves_existing_settlement_id():
    values = {
        "provider": "FLUTTERWAVE",
        "flutterwave_transaction_id": "2079349690",
        "tx_ref": "TRIVIA-223059B970C2",
        "currency": "NGN",
        "settlement_id": None,
        "raw_payload": {},
    }

    statement = _transaction_upsert_statement(values)

    compiled = str(
        statement.compile(dialect=postgresql.dialect())
    )

    update_section = compiled.split(
        "DO UPDATE SET",
        1,
    )[1]

    assert "coalesce(" in update_section.lower()
    assert "settlement_id" in update_section

def test_transaction_upsert_preserves_local_reconciliation_fields():
    values = {
        "provider": "FLUTTERWAVE",
        "flutterwave_transaction_id": "2079349690",
        "tx_ref": "TRIVIA-223059B970C2",
        "currency": "NGN",
        "local_payment_source": None,
        "local_payment_id": None,
        "settlement_id": LOCAL_SETTLEMENT_UUID,
        "is_local_payment_matched": False,
        "reconciliation_status": "UNMATCHED",
        "raw_payload": {},
    }

    statement = _transaction_upsert_statement(values)

    compiled = str(
        statement.compile(dialect=postgresql.dialect())
    )

    assert "ON CONFLICT" in compiled
    assert "DO UPDATE SET" in compiled

    update_section = compiled.split(
        "DO UPDATE SET",
        1,
    )[1]

    # Existing local reconciliation decisions must not
    # be overwritten by a subsequent settlement sync.
    assert "local_payment_source =" not in update_section
    assert "local_payment_id =" not in update_section
    assert "is_local_payment_matched =" not in update_section
    assert "reconciliation_status =" not in update_section


@pytest.mark.asyncio
async def test_matching_failure_records_failed_run():
    settlement = settlement_payload()
    settlement["transactions"] = [transaction_payload()]

    client = FakeClient(
        summaries=[settlement],
        details=[dict(settlement)],
    )

    main_session = FakeSession()
    failure_session = FakeSession()

    factory = make_session_factory([
        main_session,
        failure_session,
    ])

    with patch(
        "services.flutterwave_settlement_sync."
        "match_flutterwave_collection_transactions",
        new=AsyncMock(
            side_effect=RuntimeError(
                "Simulated matching failure"
            )
        ),
    ):
        with pytest.raises(
            RuntimeError,
            match="Simulated matching failure",
        ):
            await sync_flutterwave_settlements(
                "2026-08-13",
                "2026-08-13",
                client=client,
                session_factory=factory,
            )

    assert len(failure_session.added) == 1
    assert failure_session.added[0].run_status == "FAILED"


@pytest.mark.asyncio
async def test_fetch_transaction_payloads_requests_successful_only():
    client = FakeClient(
        transactions=[
            {
                "id": "1001",
                "tx_ref": "TRIVIA-1001",
                "status": "successful",
            },
            {
                "id": "1002",
                "tx_ref": "TRIVIA-1002",
                "status": "pending",
            },
            {
                "id": "1003",
                "tx_ref": "TRIVIA-1003",
                "status": "failed",
            },
            {
                "id": "1004",
                "tx_ref": "TRIVIA-1004",
                "status": "completed",
            },
        ]
    )

    result = await _fetch_transaction_payloads(
        client,
        "2026-11-21",
        "2026-11-21",
    )

    assert [item["id"] for item in result] == [
        "1001",
        "1004",
    ]

    client.get_all_transactions.assert_awaited_once_with(
        from_date="2026-11-21",
        to_date="2026-11-21",
        status="successful",
    )


@pytest.mark.asyncio
async def test_fetch_transaction_payloads_rejects_invalid_response():
    client = FakeClient()
    client.get_all_transactions.side_effect = None
    client.get_all_transactions.return_value = None

    with pytest.raises(
        FlutterwavePayloadError,
        match="invalid transaction list",
    ):
        await _fetch_transaction_payloads(
            client,
            "2026-11-21",
            "2026-11-21",
        )


@pytest.mark.asyncio
async def test_fetch_transaction_payloads_rejects_invalid_transaction():
    client = FakeClient(
        transactions=["not-a-transaction-object"]
    )

    with pytest.raises(
        FlutterwavePayloadError,
        match="invalid transaction object",
    ):
        await _fetch_transaction_payloads(
            client,
            "2026-11-21",
            "2026-11-21",
        )


@pytest.mark.asyncio
async def test_normalizer_keeps_independent_transaction_without_settlement():
    independent = transaction_payload()

    result = _normalize_fetched_payloads(
        settlements=[],
        independent_transactions=[independent],
    )

    assert len(result["transactions"]) == 1
    assert len(result["independent_transactions"]) == 1

    transaction = result["transactions"][0]

    assert transaction["flutterwave_transaction_id"] == "2079349690"
    assert transaction["tx_ref"] == "TRIVIA-223059B970C2"
    assert transaction["settlement_id"] is None
    assert result["skipped_missing_transaction_ids"] == []


@pytest.mark.asyncio
async def test_normalizer_deduplicates_transaction_from_both_feeds():
    settlement = settlement_payload()
    settlement["transactions"] = [transaction_payload()]

    independent = transaction_payload()

    result = _normalize_fetched_payloads(
        settlements=[settlement],
        independent_transactions=[independent],
    )

    assert len(result["settlements"]) == 1
    assert len(result["transactions"]) == 1

    # Settlement version takes priority.
    assert len(result["settlements"][0]["transactions"]) == 1
    assert result["independent_transactions"] == []

    transaction = result["transactions"][0]

    assert transaction["flutterwave_transaction_id"] == "2079349690"
    assert transaction["tx_ref"] == "TRIVIA-223059B970C2"


@pytest.mark.asyncio
async def test_successful_unsettled_transaction_is_saved_and_counted():
    transaction = {
        "id": "2096557583",
        "tx_ref": "TRIVIA-TEST-500",
        "amount": "500.00",
        "charged_amount": "500.00",
        "amount_settled": "489.25",
        "currency": "NGN",
        "status": "successful",
        "payment_type": "banktransfer",
        "created_at": "2026-11-21T19:00:00Z",
        "app_fee": "10.00",
        "merchant_fee": "0.00",
    }

    client = FakeClient(
        transactions=[transaction],
    )

    session = FakeSession()
    factory = make_session_factory([session])

    empty_matching_summary = {
        "total_transactions": 1,
        "matched_transactions": 1,
        "unmatched_transactions": 0,
        "unresolved_discrepancies": 0,
        "status_counts": {"MATCHED": 1},
        "local_payment_records_read": 1,
    }

    with patch(
        "services.flutterwave_settlement_sync."
        "match_flutterwave_collection_transactions",
        new=AsyncMock(return_value=empty_matching_summary),
    ):

        result = await sync_flutterwave_settlements(
            "2026-11-21",
            "2026-11-21",
            client=client,
            session_factory=factory,
        )

    assert result["run_status"] == "COMPLETED"
    assert result["settlements_processed"] == 0
    assert result["transactions_processed"] == 1
    assert result["gross_collections"] == Decimal("500.00")
    assert result["confirmed_net_amount"] == Decimal("0.00")

    # No settlement upsert; one independent transaction upsert.
    assert len(session.statements) == 1
    assert len(session.added) == 1

    run = session.added[0]
    assert run.gross_collections == Decimal("500.00")
    assert run.confirmed_net_amount == Decimal("0.00")
