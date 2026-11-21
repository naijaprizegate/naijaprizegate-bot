
from decimal import Decimal
from unittest import result

from services.flutterwave_reconciliation import (
    calculate_settlement_totals,
    index_local_payments,
    match_transaction,
    normalize_currency,
    normalize_reference,
    to_decimal,
)
from services.flutterwave_reconciliation import (
    separate_duplicate_flutterwave_transactions,
)

def test_normalize_reference():
    assert normalize_reference("  TRIVIA-123  ") == "TRIVIA-123"
    assert normalize_reference(None) is None
    assert normalize_reference("  ") is None


def test_normalize_currency():
    assert normalize_currency(" ngn ") == "NGN"
    assert normalize_currency(None) is None


def test_decimal_conversion():
    assert to_decimal("100.50") == Decimal("100.50")
    assert to_decimal(100) == Decimal("100.00")


def test_decimal_rejects_invalid_amount():
    import pytest

    from services.flutterwave_reconciliation import (
        ReconciliationError,
    )

    with pytest.raises(ReconciliationError):
        to_decimal("invalid")


def test_index_local_payments():
    payments = [
        {"tx_ref": "REF-001", "amount": "100"},
        {"tx_ref": "REF-002", "amount": "200"},
        {"tx_ref": "REF-001", "amount": "100"},
        {"tx_ref": None, "amount": "50"},
    ]

    indexed = index_local_payments(payments)

    assert len(indexed["REF-001"]) == 2
    assert len(indexed["REF-002"]) == 1
    assert None not in indexed


def test_successful_transaction_matches():
    local = {
        "tx_ref": "REF-001",
        "amount": "100",
        "currency": "NGN",
        "provider": "flutterwave",
        "local_status": "successful",
    }

    flutterwave = {
        "tx_ref": "REF-001",
        "amount": "100",
        "currency": "NGN",
        "status": "successful",
    }

    result = match_transaction(
        flutterwave,
        index_local_payments([local]),
    )

    assert result["status"] == "MATCHED"


def test_flw_provider_alias_can_match_flutterwave():
    local = {
        "tx_ref": "REF-001",
        "amount": "100",
        "currency": "NGN",
        "provider": "FLW",
        "local_status": "successful",
    }

    flutterwave = {
        "tx_ref": "REF-001",
        "amount": "100",
        "currency": "NGN",
        "status": "successful",
    }

    result = match_transaction(
        flutterwave,
        index_local_payments([local]),
    )

    assert result["status"] == "MATCHED"
    assert result["reason"] is None


def test_flutterwave_success_local_pending_is_flagged():
    local = {
        "tx_ref": "REF-001",
        "amount": "100",
        "currency": "NGN",
        "provider": "flutterwave",
        "local_status": "pending",
    }

    flutterwave = {
        "tx_ref": "REF-001",
        "amount": "100",
        "currency": "NGN",
        "status": "successful",
    }

    result = match_transaction(
        flutterwave,
        index_local_payments([local]),
    )

    assert result["status"] == "FLUTTERWAVE_SUCCESS_LOCAL_PENDING"
    assert result["reason"] == "LOCAL_PAYMENT_STILL_PENDING"


def test_flutterwave_success_local_failed_is_flagged():
    local = {
        "tx_ref": "REF-001",
        "amount": "100",
        "currency": "NGN",
        "provider": "flutterwave",
        "local_status": "failed",
    }

    flutterwave = {
        "tx_ref": "REF-001",
        "amount": "100",
        "currency": "NGN",
        "status": "successful",
    }

    result = match_transaction(
        flutterwave,
        index_local_payments([local]),
    )

    assert result["status"] == "FLUTTERWAVE_SUCCESS_LOCAL_FAILED"
    assert result["reason"] == "LOCAL_PAYMENT_NOT_SUCCESSFUL"


def test_providerless_local_payment_can_match():
    local = {
        "tx_ref": "REF-001",
        "amount": "100",
        "currency": "NGN",
        "provider": None,
        "local_status": "successful",
    }

    flutterwave = {
        "tx_ref": "REF-001",
        "amount": "100",
        "currency": "NGN",
        "status": "successful",
    }

    result = match_transaction(
        flutterwave,
        index_local_payments([local]),
    )

    assert result["status"] == "MATCHED"


def test_other_local_provider_is_a_conflict():
    local = {
        "tx_ref": "REF-001",
        "amount": "100",
        "currency": "NGN",
        "provider": "manual_test",
        "local_status": "successful",
    }

    flutterwave = {
        "tx_ref": "REF-001",
        "amount": "100",
        "currency": "NGN",
        "status": "successful",
    }

    result = match_transaction(
        flutterwave,
        index_local_payments([local]),
    )

    assert result["status"] == "PROVIDER_CONFLICT"
    assert result["reason"] == "LOCAL_PAYMENT_PROVIDER_CONFLICT"


def test_unknown_local_status_is_flagged():
    local = {
        "tx_ref": "REF-001",
        "amount": "100",
        "currency": "NGN",
        "provider": "flutterwave",
        "local_status": "processing",
    }

    flutterwave = {
        "tx_ref": "REF-001",
        "amount": "100",
        "currency": "NGN",
        "status": "successful",
    }

    result = match_transaction(
        flutterwave,
        index_local_payments([local]),
    )

    assert result["status"] == (
        "FLUTTERWAVE_SUCCESS_LOCAL_STATUS_UNKNOWN"
    )


def test_amount_mismatch_is_detected():
    local = {
        "tx_ref": "REF-001",
        "amount": "100",
        "currency": "NGN",
    }

    flutterwave = {
        "tx_ref": "REF-001",
        "amount": "150",
        "currency": "NGN",
        "status": "successful",
    }

    result = match_transaction(
        flutterwave,
        index_local_payments([local]),
    )

    assert result["status"] == "MISMATCH"
    assert result["reason"] == "AMOUNT_MISMATCH"


def test_currency_mismatch_is_detected():
    local = {
        "tx_ref": "REF-001",
        "amount": "100",
        "currency": "NGN",
    }

    flutterwave = {
        "tx_ref": "REF-001",
        "amount": "100",
        "currency": "USD",
        "status": "successful",
    }

    result = match_transaction(
        flutterwave,
        index_local_payments([local]),
    )

    assert result["status"] == "MISMATCH"
    assert result["reason"] == "CURRENCY_MISMATCH"


def test_duplicate_local_reference_is_ambiguous():
    payments = [
        {"tx_ref": "REF-001", "amount": "100", "currency": "NGN"},
        {"tx_ref": "REF-001", "amount": "100", "currency": "NGN"},
    ]

    flutterwave = {
        "tx_ref": "REF-001",
        "amount": "100",
        "currency": "NGN",
        "status": "successful",
    }

    result = match_transaction(
        flutterwave,
        index_local_payments(payments),
    )

    assert result["status"] == "AMBIGUOUS"
    assert result["reason"] == "DUPLICATE_LOCAL_REFERENCE"


def test_unsuccessful_flutterwave_transaction_is_not_matched():
    local = {
        "tx_ref": "REF-001",
        "amount": "100",
        "currency": "NGN",
    }

    flutterwave = {
        "tx_ref": "REF-001",
        "amount": "100",
        "currency": "NGN",
        "status": "failed",
    }

    result = match_transaction(
        flutterwave,
        index_local_payments([local]),
    )

    assert result["status"] == "UNMATCHED"
    assert result["reason"] == "FLUTTERWAVE_TRANSACTION_NOT_SUCCESSFUL"


def test_missing_local_payment_is_unmatched():
    flutterwave = {
        "tx_ref": "REF-UNKNOWN",
        "amount": "100",
        "currency": "NGN",
        "status": "successful",
    }

    result = match_transaction(flutterwave, {})

    assert result["status"] == "NO_LOCAL_PAYMENT"
    assert result["reason"] == "NO_LOCAL_PAYMENT"


def test_settlement_totals_use_actual_net_amounts():
    settlements = [
        {"gross_amount": "200.00", "net_amount": "195.69"},
        {"gross_amount": "100.00", "net_amount": "97.85"},
    ]

    result = calculate_settlement_totals(settlements)

    assert result["settlement_count"] == 2
    assert result["gross_total"] == Decimal("300.00")
    assert result["confirmed_net_total"] == Decimal("293.54")
    assert result["confirmed_net_count"] == 2
    assert result["missing_net_count"] == 0


def test_missing_net_amount_is_not_assumed():
    settlements = [
        {"gross_amount": "200.00", "net_amount": None},
    ]

    result = calculate_settlement_totals(settlements)

    assert result["gross_total"] == Decimal("200.00")
    assert result["confirmed_net_total"] == Decimal("0.00")
    assert result["missing_net_count"] == 1


def test_duplicate_flutterwave_transaction_id_is_not_counted_twice():
    transactions = [
        {"id": 101, "tx_ref": "REF-001", "amount": 100},
        {"id": 101, "tx_ref": "REF-001", "amount": 100},
    ]

    result = separate_duplicate_flutterwave_transactions(
        transactions
    )

    assert len(result["transactions"]) == 1
    assert len(result["duplicate_ids"]) == 1
    assert result["reference_conflicts"] == []


def test_same_reference_with_different_ids_is_flagged():
    transactions = [
        {"id": 101, "tx_ref": "REF-001", "amount": 100},
        {"id": 102, "tx_ref": "REF-001", "amount": 100},
    ]

    result = separate_duplicate_flutterwave_transactions(
        transactions
    )

    assert len(result["transactions"]) == 2
    assert len(result["reference_conflicts"]) == 1


def test_distinct_flutterwave_transactions_are_preserved():
    transactions = [
        {"id": 101, "tx_ref": "REF-001", "amount": 100},
        {"id": 102, "tx_ref": "REF-002", "amount": 200},
    ]

    result = separate_duplicate_flutterwave_transactions(
        transactions
    )

    assert len(result["transactions"]) == 2
    assert result["duplicate_ids"] == []
    assert result["reference_conflicts"] == []
