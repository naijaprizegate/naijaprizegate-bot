import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def configure_handler(monkeypatch, product, saved_room_code=""):
    """Configure a multiplayer payment handler with isolated in-memory state."""
    module_name = (
        "handlers.mockjamb"
        if product == "mockjamb"
        else "handlers.mockwaec"
    )

    # A dummy URL allows db.py to initialize during import.
    # The tests mock the session and do not connect to this database.
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://test:test@localhost:5432/test_db",
    )

    module = importlib.import_module(module_name)

    prefix = "mj" if product == "mockjamb" else "mw"
    payment_reference = f"test-{product}-idempotency"

    state = {
        "payment": {
            "payment_reference": payment_reference,
            "payment_status": "successful",
            "user_id": 12345,
            "course_code": "SCIENCE",
            "subject_codes_json": '["ENG", "MATH"]',
            "exam_mode": "friends",
            "invitee_count": 1,
            "required_player_count": 2,
            "room_code": saved_room_code,
        },
        "room": None,
        "host_exists": False,
        "rooms_created": 0,
        "players_added": 0,
        "host_updates": 0,
        "payment_reads": [],
    }

    async def get_payment(session, reference, **kwargs):
        state["payment_reads"].append(kwargs.copy())
        if reference != payment_reference:
            return None
        return dict(state["payment"])

    async def get_room(session, room_code):
        room = state["room"]
        if room and room["room_code"] == room_code:
            return dict(room)
        return None

    async def create_room(
        session,
        *,
        host_user_id,
        duration_minutes,
        required_player_count,
    ):
        state["rooms_created"] += 1
        state["room"] = {
            "room_code": "ROOM123",
            "host_user_id": host_user_id,
            "expected_players": required_player_count,
        }
        return dict(state["room"])

    async def get_player(session, *, room_code, user_id):
        if state["host_exists"] and user_id == 12345:
            return {
                "room_code": room_code,
                "user_id": user_id,
                "is_host": True,
            }
        return None

    async def add_player(session, **kwargs):
        state["players_added"] += 1
        state["host_exists"] = True
        return {"room_code": kwargs["room_code"], "user_id": 12345}

    async def update_player(session, **kwargs):
        state["host_updates"] += 1
        state["host_exists"] = True
        return {"room_code": kwargs["room_code"], "user_id": 12345}

    async def execute(statement, params=None):
        params = params or {}

        # Simulate saving the newly provisioned room on the payment.
        if (
            params.get("payment_reference") == payment_reference
            and params.get("room_code")
        ):
            state["payment"]["room_code"] = params["room_code"]

        return SimpleNamespace(first=lambda: (payment_reference,))

    session = SimpleNamespace(
        execute=AsyncMock(side_effect=execute),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    session_context = FakeSessionContext(session)

    monkeypatch.setattr(
        module,
        "get_async_session",
        lambda: session_context,
    )

    payment_getter = (
        "get_mockjamb_payment"
        if product == "mockjamb"
        else "get_mockwaec_payment"
    )
    room_getter = (
        "get_mockjamb_room_by_code"
        if product == "mockjamb"
        else "get_mockwaec_room_by_code"
    )
    room_creator = (
        "create_mockjamb_room"
        if product == "mockjamb"
        else "create_mockwaec_room"
    )
    player_getter = (
        "get_mockjamb_room_player"
        if product == "mockjamb"
        else "get_mockwaec_room_player"
    )
    player_adder = (
        "add_mockjamb_room_player"
        if product == "mockjamb"
        else "add_mockwaec_room_player"
    )
    player_updater = (
        "update_mockjamb_room_player_setup"
        if product == "mockjamb"
        else "update_mockwaec_room_player_setup"
    )
    player_lister = (
        "list_mockjamb_room_players"
        if product == "mockjamb"
        else "list_mockwaec_room_players"
    )
    provision_marker = (
        "mark_mockjamb_payment_provisioned"
        if product == "mockjamb"
        else "mark_mockwaec_payment_provisioned"
    )
    invite_builder = (
        "build_mockjamb_invite_link"
        if product == "mockjamb"
        else "build_mockwaec_invite_link"
    )
    waiting_text_builder = (
        "build_mockjamb_waiting_room_text"
        if product == "mockjamb"
        else "build_mockwaec_waiting_room_text"
    )
    keyboard_builder = (
        "make_mockjamb_room_waiting_keyboard"
        if product == "mockjamb"
        else "make_mockwaec_room_waiting_keyboard"
    )

    monkeypatch.setattr(module, payment_getter, AsyncMock(side_effect=get_payment))
    monkeypatch.setattr(module, room_getter, AsyncMock(side_effect=get_room))
    monkeypatch.setattr(module, room_creator, AsyncMock(side_effect=create_room))
    monkeypatch.setattr(module, player_getter, AsyncMock(side_effect=get_player))
    monkeypatch.setattr(module, player_adder, AsyncMock(side_effect=add_player))
    monkeypatch.setattr(module, player_updater, AsyncMock(side_effect=update_player))
    monkeypatch.setattr(
        module,
        player_lister,
        AsyncMock(return_value=[{"user_id": 12345, "is_host": True}]),
    )
    monkeypatch.setattr(module, provision_marker, AsyncMock())
    monkeypatch.setattr(module, invite_builder, lambda *args, **kwargs: "https://example.test/invite")
    monkeypatch.setattr(module, waiting_text_builder, lambda **kwargs: "Waiting room")
    monkeypatch.setattr(module, keyboard_builder, lambda **kwargs: None)

    if product == "mockwaec":
        monkeypatch.setattr(
            module,
            "get_mockwaec_exam_duration_minutes",
            lambda subject_count: 120,
        )

    update = SimpleNamespace(
        callback_query=None,
        effective_user=SimpleNamespace(
            first_name="Test",
            last_name="Host",
            username="testhost",
        ),
        message=SimpleNamespace(reply_text=AsyncMock(return_value=None)),
    )
    context = SimpleNamespace(
        user_data={},
        bot=SimpleNamespace(
            get_me=AsyncMock(return_value=SimpleNamespace(username="testbot"))
        ),
    )

    handler = (
        module.mockjamb_payment_success_handler
        if product == "mockjamb"
        else module.mockwaec_payment_success_handler
    )

    return SimpleNamespace(
        module=module,
        state=state,
        session=session,
        update=update,
        context=context,
        handler=handler,
        payment_reference=payment_reference,
        room_creator=getattr(module, room_creator),
        player_adder=getattr(module, player_adder),
        player_updater=getattr(module, player_updater),
        provision_marker=getattr(module, provision_marker),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("product", ["mockjamb", "mockwaec"])
async def test_repeated_success_callback_reuses_room_and_host(
    monkeypatch, product
):
    """A repeated callback must reuse the room and existing host player."""
    setup = configure_handler(monkeypatch, product)

    await setup.handler(
        setup.update, setup.context, setup.payment_reference
    )
    await setup.handler(
        setup.update, setup.context, setup.payment_reference
    )

    assert setup.state["rooms_created"] == 1
    assert setup.state["players_added"] == 1
    assert setup.state["host_updates"] == 1
    assert setup.state["payment"]["room_code"] == "ROOM123"
    assert setup.session.commit.await_count == 2
    assert setup.provision_marker.await_count == 2

    # Mock JAMB locks on its second lookup; Mock WAEC locks on its first.
    assert any(
        call.get("for_update") is True
        for call in setup.state["payment_reads"]
    )

    room_code_key = "mj_room_code" if product == "mockjamb" else "mw_room_code"
    assert setup.context.user_data[room_code_key] == "ROOM123"


@pytest.mark.asyncio
@pytest.mark.parametrize("product", ["mockjamb", "mockwaec"])
async def test_payment_cannot_reuse_room_owned_by_another_user(
    monkeypatch, product
):
    """A saved room belonging to another user must not be provisioned."""
    setup = configure_handler(
        monkeypatch,
        product,
        saved_room_code="ROOM123",
    )
    setup.state["room"] = {
        "room_code": "ROOM123",
        "host_user_id": 99999,
        "expected_players": 2,
    }

    await setup.handler(
        setup.update, setup.context, setup.payment_reference
    )

    assert setup.state["rooms_created"] == 0
    assert setup.state["players_added"] == 0
    assert setup.state["host_updates"] == 0
    setup.session.rollback.assert_awaited_once()
    setup.session.commit.assert_not_awaited()
    setup.provision_marker.assert_not_awaited()
    setup.update.message.reply_text.assert_awaited_once()
