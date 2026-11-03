import ast
from pathlib import Path
from types import SimpleNamespace

import pytest


# Load only the two helper functions from notifier.py.
# This avoids importing the bot, database, or airtime providers.
NOTIFIER_PATH = Path(__file__).resolve().parents[1] / "tasks" / "notifier.py"
source = NOTIFIER_PATH.read_text(encoding="utf-8")
tree = ast.parse(source)

helper_names = {"_classify_failure", "_resolve_success_status"}
helper_nodes = [
    node
    for node in tree.body
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    and node.name in helper_names
]

found_names = {node.name for node in helper_nodes}
missing_names = helper_names - found_names
if missing_names:
    raise RuntimeError(f"Could not find notifier helpers: {missing_names}")

isolated_module = ast.Module(body=helper_nodes, type_ignores=[])
ast.fix_missing_locations(isolated_module)

namespace = {}
exec(compile(isolated_module, str(NOTIFIER_PATH), "exec"), namespace)

classify_failure = namespace["_classify_failure"]
resolve_success_status = namespace["_resolve_success_status"]


def fake_result(*, success=False, message=None, raw=None):
    return SimpleNamespace(
        success=success,
        message=message,
        raw=raw or {},
    )


# Tests for completed and processing results.

@pytest.mark.parametrize(
    "status",
    ["completed-api", "completed", "order_completed"],
)
def test_completed_status_is_recognized(status):
    result = fake_result(success=True, raw={"status": status})
    assert resolve_success_status(result) == "completed"


@pytest.mark.parametrize(
    "status",
    ["processing-api", "queued-api", "initiated-api", "pending", "processing"],
)
def test_pending_provider_status_stays_processing(status):
    result = fake_result(success=True, raw={"status": status})
    assert resolve_success_status(result) == "processing"


def test_completed_message_is_recognized():
    result = fake_result(success=True, message="Order completed successfully")
    assert resolve_success_status(result) == "completed"


def test_success_without_confirmed_status_is_only_sent():
    result = fake_result(success=True, raw={"status": "some-other-status"})
    assert resolve_success_status(result) == "sent"


def test_unsuccessful_result_is_failed():
    result = fake_result(success=False, raw={"status": "failed"})
    assert resolve_success_status(result) == "failed"


# Tests for safe failure classification.

@pytest.mark.parametrize(
    "status",
    [
        "TIMEOUT",
        "NETWORK_ERROR",
        "PROVIDER_ERROR",
        "UNKNOWN_REQUERY_STATUS",
        "REQUERY_HTTP_ERROR",
        "UNKNOWN_NETWORK",
        "UNKNOWN_STATUS",
        "DUPLICATE_REQUEST",
        "DUPLICATE_ORDER",
    ],
)
def test_uncertain_result_needs_review_without_retry(status):
    result = fake_result(raw={"status": status, "retryable": True})
    assert classify_failure(result) == ("needs_review", False)


def test_server_error_needs_review_without_retry():
    result = fake_result(raw={"http_status": 503, "retryable": True})
    assert classify_failure(result) == ("needs_review", False)


def test_timeout_message_needs_review_without_retry():
    result = fake_result(message="Request timeout", raw={"retryable": True})
    assert classify_failure(result) == ("needs_review", False)


def test_insufficient_balance_needs_funding():
    result = fake_result(raw={"status": "INSUFFICIENT_BALANCE"})
    assert classify_failure(result) == ("failed_needs_funding", False)


def test_unsupported_status_check_needs_review():
    result = fake_result(
        raw={
            "provider_state": "unsupported",
            "failure_reason": "STATUS_CHECK_UNSUPPORTED",
        }
    )
    assert classify_failure(result) == ("needs_review", False)


def test_retryable_failure_is_not_automatically_retried():
    result = fake_result(raw={"retryable": True})
    assert classify_failure(result) == ("needs_review", False)


def test_permanent_failure_is_not_retried():
    result = fake_result(raw={"retryable": False})
    assert classify_failure(result) == ("failed_permanent", False)


def test_unknown_failure_is_not_automatically_retried():
    result = fake_result(raw={"status": "UNEXPECTED"})
    assert classify_failure(result) == ("failed_permanent", False)