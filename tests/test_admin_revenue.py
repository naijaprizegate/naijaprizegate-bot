
import ast
from pathlib import Path

import pytest


ADMIN_PATH = Path(__file__).resolve().parents[1] / "handlers" / "admin.py"
source = ADMIN_PATH.read_text(encoding="utf-8")
tree = ast.parse(source)

# Extract the fixed product_queries dictionary from admin_callback
# without importing the Telegram bot or connecting to the database.
admin_callback = next(
    node
    for node in tree.body
    if isinstance(node, ast.AsyncFunctionDef)
    and node.name == "admin_callback"
)

product_queries_node = next(
    node.value
    for node in ast.walk(admin_callback)
    if isinstance(node, ast.Assign)
    and any(
        isinstance(target, ast.Name)
        and target.id == "product_queries"
        for target in node.targets
    )
)

PRODUCT_QUERIES = ast.literal_eval(product_queries_node)


EXPECTED_PRODUCTS = {
    "Trivia": (
        "public.payments",
        "amount",
        "UPPER(status) = 'COMPLETED'",
    ),
    "JAMB": (
        "public.jamb_payments",
        "amount_paid",
        "LOWER(payment_status) = 'successful'",
    ),
    "Mock JAMB": (
        "public.mockjamb_payments",
        "amount_paid",
        "LOWER(payment_status) = 'successful'",
    ),
    "WAEC": (
        "public.waec_payments",
        "amount_paid",
        "LOWER(payment_status) = 'successful'",
    ),
    "Mock WAEC": (
        "public.mockwaec_payments",
        "amount_paid",
        "LOWER(payment_status) = 'successful'",
    ),
    "University": (
        "public.university_payments",
        "amount_paid",
        "LOWER(payment_status) = 'successful'",
    ),
}


def test_revenue_configuration_covers_exactly_six_products():
    assert PRODUCT_QUERIES == EXPECTED_PRODUCTS


@pytest.mark.parametrize(
    "product",
    ["JAMB", "Mock JAMB", "WAEC", "Mock WAEC", "University"],
)
def test_non_trivia_revenue_uses_successful_payment_status(product):
    _, _, success_condition = PRODUCT_QUERIES[product]

    assert success_condition == "LOWER(payment_status) = 'successful'"


def test_trivia_revenue_only_counts_completed_payments():
    _, _, success_condition = PRODUCT_QUERIES["Trivia"]

    assert success_condition == "UPPER(status) = 'COMPLETED'"


def test_revenue_queries_use_expected_amount_columns():
    for product, (_, amount_column, _) in PRODUCT_QUERIES.items():
        expected_column = "amount" if product == "Trivia" else "amount_paid"
        assert amount_column == expected_column


def _get_revenue_sql_template():
    """Extract the revenue SQL template from admin_callback without importing it."""
    revenue_assignment = next(
        node
        for node in ast.walk(admin_callback)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name)
            and target.id == "revenue_query"
            for target in node.targets
        )
    )

    template = revenue_assignment.value
    assert isinstance(template, ast.Call)
    assert isinstance(template.func, ast.Name)
    assert template.func.id == "sql_text"

    sql_template = template.args[0]
    assert isinstance(sql_template, ast.JoinedStr)

    # Preserve interpolated column/table/filter expressions as placeholders.
    parts = []
    for part in sql_template.values:
        if isinstance(part, ast.Constant) and isinstance(part.value, str):
            parts.append(part.value)
        elif isinstance(part, ast.FormattedValue):
            parts.append("{interpolated_value}")
    return "".join(parts)


def test_revenue_sql_has_expected_reporting_periods():
    sql = _get_revenue_sql_template()

    assert "AS total" in sql
    assert "AS month_total" in sql
    assert "AS week_total" in sql
    assert "AS today_total" in sql
    assert "AS yesterday_total" in sql
    assert "AS cycle_total" in sql


def test_yesterday_filter_has_inclusive_start_and_exclusive_end():
    sql = _get_revenue_sql_template()

    assert "created_at >= :yesterday_start" in sql
    assert "created_at < :today_start" in sql


def test_period_filters_use_created_at():
    sql = _get_revenue_sql_template()

    assert "created_at >= :month_start" in sql
    assert "created_at >= :week_start" in sql
    assert "created_at >= :today_start" in sql
    assert "created_at >= CAST(:cycle_start AS TIMESTAMPTZ)" in sql


def test_revenue_sql_uses_zero_for_empty_matching_sets():
    sql = _get_revenue_sql_template()

    # COALESCE should prevent NULL sums when no rows match a period.
    assert sql.count("COALESCE(SUM(") == 6


def test_missing_cycle_start_is_not_explicitly_guarded_in_sql():
    """
    Document the current behavior: cycle_start is passed through to SQL
    even when it is None. This test highlights the behavior for review;
    it does not claim that zero means cycle revenue is truly available.
    """
    sql = _get_revenue_sql_template()

    assert "CAST(:cycle_start AS TIMESTAMPTZ)" in sql


def test_missing_cycle_start_displays_unavailable_in_admin_callback():
    source = ADMIN_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    admin_callback_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "admin_callback"
    )

    source_segment = ast.get_source_segment(source, admin_callback_node)
    assert source_segment is not None

    assert "revenue_cycle = None if cycle_started_at is None else 0" in source_segment
    assert "if cycle_started_at is not None:" in source_segment
    assert "Unavailable (cycle start missing)" in source_segment
