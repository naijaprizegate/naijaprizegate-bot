from datetime import date
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

import services.free_trivia_service as free_trivia


@pytest.mark.asyncio
async def test_reserve_free_trivia_game_returns_game_number(monkeypatch):
    user_id = uuid4()
    nigeria_today = date(2026, 11, 8)

    monkeypatch.setattr(
        free_trivia,
        "get_nigeria_today",
        lambda: nigeria_today,
    )

    result = MagicMock()
    result.scalar_one_or_none.return_value = 1

    session = MagicMock()
    session.execute = AsyncMock(return_value=result)

    reserved = await free_trivia.reserve_free_trivia_game(
        session,
        user_id,
    )

    assert reserved == 1

    session.execute.assert_awaited_once()

    params = session.execute.await_args.args[1]

    assert params["user_id"] == user_id
    assert params["usage_date"] == nigeria_today
    assert params["daily_limit"] == 5


@pytest.mark.asyncio
async def test_sixth_free_trivia_game_is_rejected(monkeypatch):
    user_id = uuid4()

    monkeypatch.setattr(
        free_trivia,
        "get_nigeria_today",
        lambda: date(2026, 11, 8),
    )

    result = MagicMock()
    result.scalar_one_or_none.return_value = None

    session = MagicMock()
    session.execute = AsyncMock(return_value=result)

    reserved = await free_trivia.reserve_free_trivia_game(
        session,
        user_id,
    )

    assert reserved is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("games_started", "expected_remaining"),
    [
        (0, 5),
        (1, 4),
        (2, 3),
        (3, 2),
        (4, 1),
        (5, 0),
        (6, 0),
    ],
)
async def test_get_free_trivia_remaining(
    monkeypatch,
    games_started,
    expected_remaining,
):
    user_id = uuid4()

    monkeypatch.setattr(
        free_trivia,
        "get_nigeria_today",
        lambda: date(2026, 11, 8),
    )

    result = MagicMock()
    result.scalar_one_or_none.return_value = games_started

    session = MagicMock()
    session.execute = AsyncMock(return_value=result)

    remaining = await free_trivia.get_free_trivia_remaining(
        session,
        user_id,
    )

    assert remaining == expected_remaining


@pytest.mark.asyncio
async def test_new_day_without_usage_record_gets_five_games(monkeypatch):
    user_id = uuid4()

    monkeypatch.setattr(
        free_trivia,
        "get_nigeria_today",
        lambda: date(2026, 11, 9),
    )

    result = MagicMock()
    result.scalar_one_or_none.return_value = None

    session = MagicMock()
    session.execute = AsyncMock(return_value=result)

    remaining = await free_trivia.get_free_trivia_remaining(
        session,
        user_id,
    )

    assert remaining == 5