
import asyncio
from datetime import datetime, timezone
from decimal import Decimal

from services.flutterwave_payment_collector import (
    collect_local_payments,
    collect_local_successful_payments,
    normalize_local_payment,
    summarize_collected_payments,
)


def make_row(**overrides):
    row = {
        "local_payment_source": "jamb_payments",
        "local_payment_id": "123",
        "tx_ref": "JAMB-ABC123",
        "amount": Decimal("100.00"),
        "currency": "NGN",
        "currency_source": "CHECKOUT_CONFIGURATION",
        "provider": "flutterwave",
        "local_status": "successful",
        "created_at": datetime(2026, 4, 1, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return row


def test_flutterwave_provider_is_candidate_for_api_verification():
    record = normalize_local_payment(make_row())

    assert record["provider"] == "flutterwave"
    assert record["collection_eligibility"] == (
        "CANDIDATE_FOR_API_VERIFICATION"
    )
    assert record["reconciliation_status"] == (
        "PENDING_FLUTTERWAVE_VERIFICATION"
    )

def test_pending_local_payment_is_collected_for_reconciliation():
    record = normalize_local_payment(
        make_row(local_status="pending")
    )

    assert record["local_status"] == "pending"
    assert record["collection_eligibility"] == (
        "CANDIDATE_FOR_API_VERIFICATION"
    )
    assert record["reconciliation_status"] == (
        "PENDING_FLUTTERWAVE_VERIFICATION"
    )

def test_missing_provider_is_not_assumed_to_be_flutterwave():
    record = normalize_local_payment(make_row(provider=None))

    assert record["provider"] is None
    assert record["provider_attribution"] == "PROVIDER_NOT_RECORDED"
    assert record["collection_eligibility"] == "UNVERIFIED_PROVIDER"
    assert record["reconciliation_status"] == (
        "UNRESOLVED_PROVIDER_ATTRIBUTION"
    )


def test_manual_test_provider_is_excluded():
    record = normalize_local_payment(make_row(provider="manual_test"))

    assert record["collection_eligibility"] == (
        "EXCLUDED_NON_FLUTTERWAVE"
    )
    assert record["reconciliation_status"] == (
        "EXCLUDED_NON_FLUTTERWAVE"
    )


def test_missing_reference_is_unresolved():
    record = normalize_local_payment(make_row(tx_ref="  "))

    assert record["tx_ref"] is None
    assert record["reconciliation_status"] == (
        "UNRESOLVED_MISSING_REFERENCE"
    )


def test_amount_is_normalized_to_decimal():
    record = normalize_local_payment(make_row(amount="100.5"))

    assert record["amount"] == Decimal("100.50")
    assert isinstance(record["amount"], Decimal)


def test_currency_provenance_is_preserved():
    record = normalize_local_payment(make_row())

    assert record["currency"] == "NGN"
    assert record["currency_source"] == "CHECKOUT_CONFIGURATION"


def test_summary_separates_candidates_and_unresolved_records():
    records = [
        normalize_local_payment(make_row(
            local_payment_id="1",
            amount="100",
            provider="flutterwave",
        )),
        normalize_local_payment(make_row(
            local_payment_id="2",
            amount="200",
            provider=None,
        )),
        normalize_local_payment(make_row(
            local_payment_id="3",
            amount="50",
            provider="manual_test",
        )),
        normalize_local_payment(make_row(
            local_payment_id="4",
            amount="75",
            provider=None,
            tx_ref=None,
        )),
    ]

    summary = summarize_collected_payments(records)

    assert summary["total_records_read"] == 4
    assert summary["candidate_count"] == 1
    assert summary["candidate_amount"] == Decimal("100.00")
    assert summary["excluded_count"] == 1
    assert summary["excluded_amount"] == Decimal("50.00")
    assert summary["unresolved_reference_count"] == 1
    assert summary["unresolved_provider_count"] == 1


def test_database_collector_reads_all_statuses_without_writing():
    class FakeMappings:
        def all(self):
            return [
                make_row(local_status="pending"),
                make_row(
                    local_payment_id="124",
                    tx_ref="JAMB-XYZ456",
                    local_status="successful",
                ),
            ]

    class FakeResult:
        def mappings(self):
            return FakeMappings()

    class FakeSession:
        def __init__(self):
            self.statement = None

        async def execute(self, statement):
            self.statement = statement
            return FakeResult()

    async def scenario():
        session = FakeSession()

        records = await collect_local_payments(session)

        assert len(records) == 2
        assert records[0]["local_status"] == "pending"
        assert records[1]["local_status"] == "successful"

        sql = str(session.statement).strip().upper()

        assert sql.startswith("SELECT")
        assert "UNION ALL" in sql
        assert "UPDATE " not in sql
        assert "DELETE " not in sql
        assert "INSERT " not in sql
        assert "WHERE LOWER(TRIM(COALESCE(STATUS" not in sql

    asyncio.run(scenario())


def test_backward_compatible_collector_alias():
    class FakeMappings:
        def all(self):
            return [make_row(local_status="pending")]

    class FakeResult:
        def mappings(self):
            return FakeMappings()

    class FakeSession:
        async def execute(self, statement):
            return FakeResult()

    async def scenario():
        records = await collect_local_successful_payments(FakeSession())

        assert len(records) == 1
        assert records[0]["local_status"] == "pending"

    asyncio.run(scenario())
