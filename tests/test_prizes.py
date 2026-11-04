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

def test_prize_field_callback_data_stays_within_telegram_limit():
    """Prize field buttons must use valid Telegram callback data."""
    prize_id = "793a4bca-ef7f-496c-8933-0697f057f2c2"

    callbacks = {
        "name": f"prize_admin:f:n:{prize_id}",
        "description": f"prize_admin:f:d:{prize_id}",
        "photo": f"prize_admin:f:p:{prize_id}",
    }

    # Every callback must fit Telegram's 64-byte limit.
    for field, callback in callbacks.items():
        assert 1 <= len(callback.encode("utf-8")) <= 64, (
            f"{field} callback exceeds Telegram's 64-byte limit"
        )

    # Confirm the production keyboard uses these shortened formats.
    for code in ("n", "d", "p"):
        assert f'callback_data=f"prize_admin:f:{code}:{{prize.id}}"' in SOURCE

    # Confirm the handler is registered to receive the shortened callbacks.
    assert (
        r'pattern=r"^prize_admin:f:(n|d|p):[0-9a-fA-F-]{36}$"'
        in SOURCE
    )

def test_admin_prize_edit_keeps_photo_caption_short():
    """Long prize details must be sent separately from the photo."""
    handler = _get_async_function("admin_prize_edit")

    send_photo_calls = [
        node
        for node in ast.walk(handler)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "send_photo"
    ]

    send_message_calls = [
        node
        for node in ast.walk(handler)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "send_message"
    ]

    assert send_photo_calls, "Prize photo must still be displayed."
    assert send_message_calls, (
        "Prize details must be sent separately."
    )

    photo_call = send_photo_calls[0]

    caption = next(
        keyword.value
        for keyword in photo_call.keywords
        if keyword.arg == "caption"
    )

    # The photo caption must contain only the escaped prize name.
    assert isinstance(caption, ast.JoinedStr)
    assert "_escape(prize.name)" in ast.unparse(caption)

    # Admin details must use the chunked formatting helper.
    source = ast.unparse(handler)

    assert "_admin_details_chunks(prize)" in source
    assert "text=chunk" in source
    assert "chunk_index == len(chunks) - 1" in source
    assert "reply_markup=reply_markup" in source

def test_public_prize_display_receives_context():
    """Public prize display must receive context for message tracking."""
    handler = _get_async_function("_show_public_prize")

    arguments = [arg.arg for arg in handler.args.args]

    assert arguments[:4] == [
        "update",
        "context",
        "index",
        "edit",
    ]


def test_public_prize_photo_caption_is_short():
    """Public prize photos must not contain the full prize description."""
    handler = _get_async_function("_show_public_prize")

    send_photo_calls = [
        node
        for node in ast.walk(handler)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "send_photo"
    ]

    assert send_photo_calls, "Public prize photo must still be displayed."

    photo_call = send_photo_calls[0]

    caption = next(
        keyword.value
        for keyword in photo_call.keywords
        if keyword.arg == "caption"
    )

    assert "_escape(prize.name)" in ast.unparse(caption)


def test_public_prize_details_use_chunks_and_navigation():
    """Full details must be separate and navigation attached to final chunk."""
    handler = _get_async_function("_show_public_prize")
    source = ast.unparse(handler)

    assert "_public_details_chunks" in source
    assert "chat.send_message" in source
    assert "detail_message_ids.append" in source
    assert "chunk_index == len(detail_chunks) - 1" in source
    assert "reply_markup=reply_markup" in source


def test_public_prize_navigation_preserves_context():
    """Opening and paging must pass context to the display function."""
    for function_name in (
        "public_prizes_open",
        "public_prizes_page",
    ):
        handler = _get_async_function(function_name)

        calls = [
            node
            for node in ast.walk(handler)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_show_public_prize"
        ]

        assert calls, f"{function_name} must call _show_public_prize."

        assert any(
            len(call.args) >= 3
            and isinstance(call.args[1], ast.Name)
            and call.args[1].id == "context"
            for call in calls
        ), f"{function_name} must pass context."


def test_public_prize_main_menu_cleans_up_catalogue_messages():
    """Returning to Main Menu must clean up the associated prize messages."""
    handler = _get_async_function("public_prizes_main_menu")
    source = ast.unparse(handler)

    assert "_delete_public_catalogue_messages" in source
    assert "_main_menu_keyboard" in source
    assert "keep_message_id" in source