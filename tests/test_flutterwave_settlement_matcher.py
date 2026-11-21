
from types import SimpleNamespace

import pytest

from services import flutterwave_settlement_matcher as matcher


def make_local_payment(
    *,
    source="payments",
    payment_id="101",
    provider="flutterwave",
    status="successful",
    amount="100.00",
    currency="NGN",
):
    return {
        "local_payment_source": source,
        "local_payment_id": payment_id,
        "provider": provider,
        "local_status": status,
        "amount": amount,
        "currency": currency,
        "tx_ref": "TEST-REF-001",
    }


def make_flw_record():
    return SimpleNamespace(
        provider="FLUTTERWAVE",
        flutterwave_transaction_id="123456",
        tx_ref="TEST-REF-001",
        charged_amount=100,
        currency="NGN",
        transaction_status="successful",
        local_payment_source=None,
        local_payment_id=None,
        is_local_payment_matched=False,
        reconciliation_status="UNMATCHED",
    )


def test_transaction_mapping_uses_charged_amount():
    record = make_flw_record()

    result = matcher._transaction_for_matching(record)

    assert result == {
        "id": "123456",
        "tx_ref": "TEST-REF-001",
        "amount": 100,
        "currency": "NGN",
        "status": "successful",
    }


def test_exact_match_assigns_local_payment():
    record = make_flw_record()
    local = make_local_payment()

    result = {
        "status": "MATCHED",
        "reason": None,
        "local_payment": local,
    }

    status = matcher._assign_match_result(record, result)

    assert status == "MATCHED"
    assert record.local_payment_source == "payments"
    assert record.local_payment_id == "101"
    assert record.is_local_payment_matched is True
    assert record.reconciliation_status == "MATCHED"


def test_providerless_match_keeps_engine_status():
    record = make_flw_record()
    local = make_local_payment(provider=None)

    result = {
        "status": "MATCHED",
        "reason": None,
        "local_payment": local,
    }

    status = matcher._assign_match_result(record, result)

    assert status == "MATCHED"
    assert record.is_local_payment_matched is True
    assert record.reconciliation_status == "MATCHED"


@pytest.mark.parametrize(
    "status",
    [
        "FLUTTERWAVE_SUCCESS_LOCAL_PENDING",
        "FLUTTERWAVE_SUCCESS_LOCAL_FAILED",
        "FLUTTERWAVE_SUCCESS_LOCAL_STATUS_UNKNOWN",
        "MISMATCH",
        "PROVIDER_CONFLICT",
        "NO_LOCAL_PAYMENT",
        "AMBIGUOUS",
        "UNMATCHED",
    ],
)
def test_nonmatched_results_do_not_set_matched_flag(status):
    record = make_flw_record()

    local = (
        make_local_payment()
        if status in {
            "FLUTTERWAVE_SUCCESS_LOCAL_PENDING",
            "FLUTTERWAVE_SUCCESS_LOCAL_FAILED",
            "FLUTTERWAVE_SUCCESS_LOCAL_STATUS_UNKNOWN",
            "MISMATCH",
            "PROVIDER_CONFLICT",
        }
        else None
    )

    result = {
        "status": status,
        "reason": None,
        "local_payment": local,
    }

    returned_status = matcher._assign_match_result(record, result)

    assert returned_status == status
    assert record.is_local_payment_matched is False
    assert record.reconciliation_status == status


def test_no_local_payment_clears_previous_assignment():
    record = make_flw_record()
    record.local_payment_source = "payments"
    record.local_payment_id = "101"
    record.is_local_payment_matched = True

    result = {
        "status": "NO_LOCAL_PAYMENT",
        "reason": "NO_LOCAL_PAYMENT",
        "local_payment": None,
    }

    matcher._assign_match_result(record, result)

    assert record.local_payment_source is None
    assert record.local_payment_id is None
    assert record.is_local_payment_matched is False


def test_summary_counts_only_exact_matches():
    summary = matcher.summarize_match_results(
        [
            "MATCHED",
            "MATCHED",
            "FLUTTERWAVE_SUCCESS_LOCAL_PENDING",
            "NO_LOCAL_PAYMENT",
            "MISMATCH",
        ]
    )

    assert summary["total_transactions"] == 5
    assert summary["matched_transactions"] == 2
    assert summary["unmatched_transactions"] == 3
    assert summary["unresolved_discrepancies"] == 3
    assert summary["status_counts"]["MATCHED"] == 2


def test_empty_summary():
    summary = matcher.summarize_match_results([])

    assert summary["total_transactions"] == 0
    assert summary["matched_transactions"] == 0
    assert summary["unmatched_transactions"] == 0


from unittest.mock import AsyncMock, patch


class FakeScalars:
    def __init__(self, records):
        self.records = records

    def all(self):
        return self.records


class FakeResult:
    def __init__(self, records):
        self.records = records

    def scalars(self):
        return FakeScalars(self.records)


@pytest.mark.asyncio
async def test_async_matcher_updates_flutterwave_records_only():
    record = make_flw_record()

    local_payment = make_local_payment()

    session = SimpleNamespace(
        execute=AsyncMock(
            return_value=FakeResult([record])
        ),
        flush=AsyncMock(),
    )

    with patch.object(
        matcher,
        "collect_local_payments",
        new=AsyncMock(return_value=[local_payment]),
    ):
        summary = await (
            matcher.match_flutterwave_collection_transactions(
                session
            )
        )

    assert record.reconciliation_status == "MATCHED"
    assert record.is_local_payment_matched is True
    assert record.local_payment_source == "payments"
    assert record.local_payment_id == "101"

    assert summary["total_transactions"] == 1
    assert summary["matched_transactions"] == 1
    assert summary["unmatched_transactions"] == 0

    session.execute.assert_awaited_once()
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_async_matcher_handles_no_local_payment():
    record = make_flw_record()

    session = SimpleNamespace(
        execute=AsyncMock(
            return_value=FakeResult([record])
        ),
        flush=AsyncMock(),
    )

    with patch.object(
        matcher,
        "collect_local_payments",
        new=AsyncMock(return_value=[]),
    ):
        summary = await (
            matcher.match_flutterwave_collection_transactions(
                session
            )
        )

    assert record.reconciliation_status == "NO_LOCAL_PAYMENT"
    assert record.is_local_payment_matched is False
    assert record.local_payment_source is None
    assert record.local_payment_id is None

    assert summary["total_transactions"] == 1
    assert summary["matched_transactions"] == 0
    assert summary["unmatched_transactions"] == 1


@pytest.mark.asyncio
async def test_async_matcher_does_not_commit_session():
    record = make_flw_record()

    session = SimpleNamespace(
        execute=AsyncMock(
            return_value=FakeResult([record])
        ),
        flush=AsyncMock(),
    )

    with patch.object(
        matcher,
        "collect_local_payments",
        new=AsyncMock(return_value=[make_local_payment()]),
    ):
        await matcher.match_flutterwave_collection_transactions(
            session
        )

    session.flush.assert_awaited_once()
    assert not hasattr(session, "commit")
