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


@pytest.mark.asyncio
async def test_lobby_does_not_refresh_after_challenge_starts(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://test:test@localhost:5432/test_db",
    )

    module = importlib.import_module("handlers.challenge")

    players_result = SimpleNamespace(fetchall=lambda: [])

    challenge_result = SimpleNamespace(
        fetchone=lambda: SimpleNamespace(
            creator_id=12345,
            status="selecting_category",
        )
    )

    session = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[players_result, challenge_result]
        )
    )

    monkeypatch.setattr(
        module,
        "AsyncSessionLocal",
        lambda: FakeSessionContext(session),
    )

    bot = SimpleNamespace(
        send_message=AsyncMock(),
        edit_message_text=AsyncMock(),
    )

    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=12345),
        effective_chat=SimpleNamespace(id=100),
    )

    context = SimpleNamespace(bot=bot)

    await module.show_challenge_lobby(
        update,
        context,
        challenge_id=10,
    )

    assert session.execute.await_count == 2
    bot.send_message.assert_not_awaited()
    bot.edit_message_text.assert_not_awaited()

@pytest.mark.asyncio
async def test_joined_player_refreshes_every_lobby_and_only_host_gets_controls(
    monkeypatch,
):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://test:test@localhost:5432/test_db",
    )

    module = importlib.import_module("handlers.challenge")

    state = {
        "lobby_messages": [
            SimpleNamespace(
                user_id=111,
                chat_id=1001,
                message_id=5001,
            )
        ]
    }

    players = [
        SimpleNamespace(
            tg_id=111,
            username="Host",
            full_name="Host Player",
        ),
        SimpleNamespace(
            tg_id=222,
            username="Invitee",
            full_name="Invitee Player",
        ),
    ]

    async def execute(statement, params=None):
        sql = str(statement).lower()

        if "from challenge_players cp" in sql:
            return SimpleNamespace(fetchall=lambda: players)

        if "select creator_id, status" in sql:
            return SimpleNamespace(
                fetchone=lambda: SimpleNamespace(
                    creator_id=111,
                    status="waiting",
                )
            )

        if (
            "from challenge_lobby_messages" in sql
            and "and user_id" in sql
        ):
            return SimpleNamespace(fetchone=lambda: None)

        if "insert into challenge_lobby_messages" in sql:
            state["lobby_messages"].append(
                SimpleNamespace(
                    user_id=params["uid"],
                    chat_id=params["chat_id"],
                    message_id=params["message_id"],
                )
            )
            return SimpleNamespace()

        if "select user_id, chat_id, message_id" in sql:
            return SimpleNamespace(
                fetchall=lambda: list(state["lobby_messages"])
            )

        raise AssertionError(f"Unexpected SQL: {sql}")

    session = SimpleNamespace(
        execute=AsyncMock(side_effect=execute),
        commit=AsyncMock(),
    )

    monkeypatch.setattr(
        module,
        "AsyncSessionLocal",
        lambda: FakeSessionContext(session),
    )

    bot = SimpleNamespace(
        send_message=AsyncMock(
            return_value=SimpleNamespace(message_id=6002)
        ),
        edit_message_text=AsyncMock(),
    )

    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=222),
        effective_chat=SimpleNamespace(id=2002),
    )

    context = SimpleNamespace(bot=bot)

    await module.show_challenge_lobby(
        update,
        context,
        challenge_id=10,
    )

    # A lobby is created for the joining player.
    bot.send_message.assert_awaited_once()

    # Both the host's and invitee's lobby messages are refreshed.
    assert bot.edit_message_text.await_count == 2

    edited_messages = {
        call.kwargs["message_id"]: call.kwargs
        for call in bot.edit_message_text.await_args_list
    }

    assert 5001 in edited_messages
    assert 6002 in edited_messages

    # Both messages show the updated player list.
    for message in edited_messages.values():
        assert "Players Joined (2)" in message["text"]
        assert "Host" in message["text"]
        assert "Invitee" in message["text"]

    # Only the host receives Start and Back buttons.
    host_keyboard = edited_messages[5001]["reply_markup"].inline_keyboard
    invitee_keyboard = edited_messages[6002]["reply_markup"]

    host_callbacks = [
        button.callback_data
        for row in host_keyboard
        for button in row
    ]

    assert f"challenge_start_10" in host_callbacks
    assert f"challenge_back_confirm_10" in host_callbacks
    assert invitee_keyboard is None

@pytest.mark.asyncio
async def test_join_rejected_after_host_starts_challenge(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://test:test@localhost:5432/test_db",
    )

    module = importlib.import_module("handlers.challenge")

    session = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(
                fetchone=lambda: SimpleNamespace(
                    status="selecting_category"
                )
            )
        ),
        commit=AsyncMock(),
    )

    monkeypatch.setattr(
        module,
        "AsyncSessionLocal",
        lambda: FakeSessionContext(session),
    )

    upsert_user = AsyncMock()
    monkeypatch.setattr(
        module,
        "upsert_telegram_user",
        upsert_user,
    )

    chat = SimpleNamespace(send_message=AsyncMock())

    update = SimpleNamespace(
        effective_user=SimpleNamespace(
            id=333,
            username="LatePlayer",
            full_name="Late Player",
        ),
        effective_chat=chat,
    )

    context = SimpleNamespace(args=["challenge_10"])

    result = await module.join_challenge(update, context)

    assert result is False
    upsert_user.assert_awaited_once()
    session.execute.assert_awaited_once()
    session.commit.assert_not_awaited()
    chat.send_message.assert_awaited_once_with(
        "⚠️ This challenge is no longer accepting players."
    )

@pytest.mark.asyncio
async def test_host_can_start_with_two_players_and_close_registration(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://test:test@localhost:5432/test_db",
    )

    module = importlib.import_module("handlers.challenge")

    statements = []

    async def execute(statement, params=None):
        sql = str(statement).lower()
        statements.append((sql, params))

        if "select creator_id, status" in sql:
            return SimpleNamespace(
                fetchone=lambda: SimpleNamespace(
                    creator_id=111,
                    status="waiting",
                )
            )

        if "select count(*) as total_players" in sql:
            return SimpleNamespace(
                fetchone=lambda: SimpleNamespace(total_players=2)
            )

        return SimpleNamespace()

    session = SimpleNamespace(
        execute=AsyncMock(side_effect=execute),
        commit=AsyncMock(),
    )

    monkeypatch.setattr(
        module,
        "AsyncSessionLocal",
        lambda: FakeSessionContext(session),
    )

    query = SimpleNamespace(
        data="challenge_start_10",
        from_user=SimpleNamespace(id=111),
        message=SimpleNamespace(chat_id=1001),
        answer=AsyncMock(),
    )

    bot = SimpleNamespace(send_message=AsyncMock())

    update = SimpleNamespace(callback_query=query)
    context = SimpleNamespace(bot=bot)

    await module.start_challenge(update, context)

    assert session.commit.await_count == 1

    status_updates = [
        (sql, params)
        for sql, params in statements
        if "update challenges" in sql
    ]

    assert len(status_updates) == 1
    assert "selecting_category" in status_updates[0][0]

    bot.send_message.assert_awaited_once()

    sent_message = bot.send_message.await_args.kwargs

    assert sent_message["chat_id"] == 1001
    assert "Select a category" in sent_message["text"]
    assert sent_message["reply_markup"] is not None

@pytest.mark.asyncio
async def test_host_category_selection_moves_challenge_to_in_progress(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://test:test@localhost:5432/test_db",
    )

    module = importlib.import_module("handlers.challenge")

    statements = []

    async def execute(statement, params=None):
        sql = str(statement).lower()
        statements.append((sql, params))

        if "select creator_id, status" in sql:
            return SimpleNamespace(
                fetchone=lambda: SimpleNamespace(
                    creator_id=111,
                    status="selecting_category",
                )
            )

        return SimpleNamespace()

    session = SimpleNamespace(
        execute=AsyncMock(side_effect=execute),
        commit=AsyncMock(),
    )

    monkeypatch.setattr(
        module,
        "AsyncSessionLocal",
        lambda: FakeSessionContext(session),
    )

    generate_questions = AsyncMock(return_value=[{"id": 1}])
    send_question = AsyncMock()

    monkeypatch.setattr(
        module,
        "generate_challenge_questions",
        generate_questions,
    )
    monkeypatch.setattr(
        module,
        "send_challenge_question",
        send_question,
    )

    query = SimpleNamespace(
        data="challenge_category_10|geography",
        from_user=SimpleNamespace(id=111),
        answer=AsyncMock(),
        message=SimpleNamespace(edit_text=AsyncMock()),
    )

    update = SimpleNamespace(callback_query=query)
    context = SimpleNamespace()

    await module.choose_challenge_category(update, context)

    status_updates = [
        (sql, params)
        for sql, params in statements
        if "update challenges" in sql
    ]

    assert len(status_updates) == 1
    assert "in_progress" in status_updates[0][0]
    assert status_updates[0][1]["category"] == "geography"

    generate_questions.assert_awaited_once()
    send_question.assert_awaited_once_with(
        context=context,
        challenge_id=10,
        question_order=1,
    )
    query.answer.assert_awaited_once()
    query.message.edit_text.assert_awaited_once()