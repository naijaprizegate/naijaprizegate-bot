from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import waec_payment_finalizer


@pytest.mark.asyncio
async def test_waec_finalizer_records_timestamps_after_crediting(monkeypatch):
    payment = {
        "payment_reference": "test-waec-001",
        "user_id": 12345,
        "amount_paid": 100,
        "question_credits_added": 10,
        "mock_sessions_added": 0,
        "payment_status": "pending",
    }

    latest_payment = {
        **payment,
        "payment_status": "successful",
        "verified_at": "verified-time",
        "credited_at": "credited-time",
        "processed_at": "processed-time",
    }

    monkeypatch.setattr(
        waec_payment_finalizer,
        "get_waec_payment",
        AsyncMock(side_effect=[payment, latest_payment]),
    )

    claim_result = SimpleNamespace(first=lambda: ("test-waec-001",))
    insert_result = SimpleNamespace()
    benefit_result = SimpleNamespace(rowcount=1)
    timestamp_result = SimpleNamespace()

    session = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                claim_result,
                insert_result,
                benefit_result,
                timestamp_result,
            ]
        )
    )

    did_credit, result, credits, mock_sessions = (
        await waec_payment_finalizer.finalize_waec_payment(
            session,
            payment_reference="test-waec-001",
            user_id=12345,
            amount_paid=100,
        )
    )

    assert did_credit is True
    assert result["verified_at"] == "verified-time"
    assert result["credited_at"] == "credited-time"
    assert result["processed_at"] == "processed-time"
    assert credits == 10
    assert mock_sessions == 0

    assert session.execute.await_count == 4

    claim_sql = str(session.execute.await_args_list[0].args[0])
    assert "verified_at = COALESCE(verified_at, now())" in claim_sql

    timestamp_sql = str(session.execute.await_args_list[3].args[0])
    assert "credited_at = COALESCE(credited_at, now())" in timestamp_sql
    assert "processed_at = COALESCE(processed_at, now())" in timestamp_sql


@pytest.mark.asyncio
async def test_waec_finalizer_does_not_mark_processed_if_benefit_update_fails(
    monkeypatch,
):
    payment = {
        "payment_reference": "test-waec-002",
        "user_id": 12345,
        "amount_paid": 100,
        "question_credits_added": 10,
        "mock_sessions_added": 0,
        "payment_status": "pending",
    }

    monkeypatch.setattr(
        waec_payment_finalizer,
        "get_waec_payment",
        AsyncMock(return_value=payment),
    )

    claim_result = SimpleNamespace(first=lambda: ("test-waec-002",))
    insert_result = SimpleNamespace()
    benefit_result = SimpleNamespace(rowcount=0)

    session = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                claim_result,
                insert_result,
                benefit_result,
            ]
        )
    )

    with pytest.raises(
        RuntimeError,
        match="WAEC payment benefit update failed",
    ):
        await waec_payment_finalizer.finalize_waec_payment(
            session,
            payment_reference="test-waec-002",
            user_id=12345,
            amount_paid=100,
        )

    # The timestamp-completion query must not run after a failed benefit update.
    assert session.execute.await_count == 3


@pytest.mark.asyncio
async def test_waec_finalizer_does_not_credit_an_already_successful_payment(
    monkeypatch,
):
    payment = {
        "payment_reference": "test-waec-003",
        "user_id": 12345,
        "amount_paid": 100,
        "question_credits_added": 10,
        "mock_sessions_added": 0,
        "payment_status": "successful",
    }

    monkeypatch.setattr(
        waec_payment_finalizer,
        "get_waec_payment",
        AsyncMock(return_value=payment),
    )

    claim_result = SimpleNamespace(first=lambda: None)

    session = SimpleNamespace(
        execute=AsyncMock(return_value=claim_result)
    )

    did_credit, result, credits, mock_sessions = (
        await waec_payment_finalizer.finalize_waec_payment(
            session,
            payment_reference="test-waec-003",
            user_id=12345,
            amount_paid=100,
        )
    )

    assert did_credit is False
    assert result["payment_status"] == "successful"
    assert credits == 10
    assert mock_sessions == 0

    # Only the status-claim query runs; the benefit is not credited again.
    assert session.execute.await_count == 1
