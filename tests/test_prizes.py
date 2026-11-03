import ast
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRIZES_PATH = PROJECT_ROOT / "handlers" / "prizes.py"

SOURCE = PRIZES_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _get_function(name):
    """Find a function by name without importing the bot."""
    return next(
        node
        for node in TREE.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    )


def _get_async_function(name):
    """Find an async function by name without importing the bot."""
    return next(
        node
        for node in TREE.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == name
    )


@pytest.mark.parametrize(
    "function_name",
    [
        "admin_prizes_open",
        "admin_prizes_list",
        "admin_prize_edit",
        "admin_prize_toggle",
        "admin_prize_delete",
        "admin_prize_delete_confirm",
        "start_add_prize",
        "receive_name",
        "receive_description",
        "receive_conditions",
        "receive_photo",
        "start_edit_field",
        "receive_edit_value",
        "cancel_prize_operation",
        "cancel_prize_button",
        "back_to_admin",
    ],
)
def test_admin_handlers_check_authorization(function_name):
    """Every sensitive admin handler must check authorization."""
    function_node = _get_async_function(function_name)

    authorization_checks = [
        node
        for node in ast.walk(function_node)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_authorized"
    ]

    assert authorization_checks, (
        f"{function_name} must check _authorized(update)"
    )


@pytest.mark.parametrize(
    ("callback_pattern", "handler_name"),
    [
        (r"^prizes:open$", "public_prizes_open"),
        (r"^prizes:page:\d+$", "public_prizes_page"),
        (r"^prizes:main_menu$", "public_prizes_main_menu"),
        (r"^prize_admin:open$", "admin_prizes_open"),
        (r"^prize_admin:list$", "admin_prizes_list"),
        (
            r"^prize_admin:edit:[0-9a-fA-F-]{36}$",
            "admin_prize_edit",
        ),
        (
            r"^prize_admin:toggle:[0-9a-fA-F-]{36}$",
            "admin_prize_toggle",
        ),
        (
            r"^prize_admin:delete:[0-9a-fA-F-]{36}$",
            "admin_prize_delete",
        ),
        (
            r"^prize_admin:delete_confirm$",
            "admin_prize_delete_confirm",
        ),
        (r"^prize_admin:back$", "back_to_admin"),
    ],
)
def test_callback_patterns_are_registered(callback_pattern, handler_name):
    """Important public and admin callbacks must be registered."""
    registrations = [
        node
        for node in ast.walk(_get_function("register_handlers"))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "CallbackQueryHandler"
    ]

    def matches_registration(node):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "CallbackQueryHandler"
        ):
            return False

        handler_matches = (
            len(node.args) >= 1
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == handler_name
        )

        pattern_matches = any(
            keyword.arg == "pattern"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value == callback_pattern
            for keyword in node.keywords
        )

        return handler_matches and pattern_matches

    assert any(
        matches_registration(node)
        for node in registrations
    ), f"Missing callback registration: {handler_name}"


def test_public_catalogue_only_queries_active_prizes_when_requested():
    function_node = _get_async_function("_get_prizes")

    active_filter_found = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "where"
        for node in ast.walk(function_node)
    )

    assert active_filter_found, (
        "_get_prizes(active_only=True) must filter inactive prizes"
    )


def test_catalogue_does_not_contain_prize_awarding_or_payment_logic():
    """Catalogue management must remain separate from rewards and payments."""
    forbidden_imports = {
        "handlers.payments",
        "services.jamb_payments",
        "services.playtrivia",
    }

    imported_modules = set()

    for node in TREE.body:
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imported_modules.add(module)

    assert not (forbidden_imports & imported_modules)


def test_public_navigation_has_previous_next_and_main_menu_callbacks():
    """The public keyboard must provide navigation and a route back."""
    keyboard_function = _get_function("_public_keyboard")

    callback_values = {
        node.value
        for node in ast.walk(keyboard_function)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
    }

    page_constant_exists = any(
        isinstance(node, ast.Name) and node.id == "PUBLIC_PAGE"
        for node in ast.walk(keyboard_function)
    )
    assert page_constant_exists or any(
        "prizes:page:" in value for value in callback_values
    )
    main_menu_constant_exists = any(
        isinstance(node, ast.Name) and node.id == "PUBLIC_MAIN_MENU"
        for node in ast.walk(keyboard_function)
    )
    assert main_menu_constant_exists or (
        "prizes:main_menu" in callback_values
    )


def test_navigation_fallback_helper_has_replacement_path():
    """The fallback should edit, then delete and replace a message if needed."""
    helper = _get_async_function("_edit_or_replace_text_message")

    called_methods = {
        node.func.attr
        for node in ast.walk(helper)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
    }

    assert "edit_message_text" in called_methods
    assert "delete" in called_methods
    assert "send_message" in called_methods
    assert any(
        isinstance(node, ast.Try)
        for node in ast.walk(helper)
    )


@pytest.mark.parametrize(
    "handler_name",
    [
        "public_prizes_main_menu",
        "admin_prizes_open",
        "admin_prizes_list",
        "back_to_admin",
    ],
)
def test_navigation_handlers_use_replacement_fallback(handler_name):
    """Navigation handlers should use the shared message-edit fallback."""
    handler = _get_async_function(handler_name)

    assert any(
        isinstance(node, ast.Name)
        and node.id == "_edit_or_replace_text_message"
        for node in ast.walk(handler)
    )
