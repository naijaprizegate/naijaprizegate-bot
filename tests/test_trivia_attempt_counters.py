
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import helpers


@pytest.mark.asyncio
async def test_add_paid_tries_does_not_increment_played_counters(monkeypatch):
    user = SimpleNamespace(
        id=10,
        tg_id=12345,
        tries_paid=2,
        tries_bonus=3,
    )

    game_state = SimpleNamespace(
        paid_tries_this_cycle=100,
        lifetime_paid_tries=500,
    )

    global_counter = SimpleNamespace(paid_tries_total=1000)

    result = SimpleNamespace(
        scalar_one_or_none=lambda: user
    )

    session = SimpleNamespace(
        execute=AsyncMock(return_value=result),
        add=lambda obj: None,
        flush=AsyncMock(),
    )

    ensure_global_counter = AsyncMock(return_value=global_counter)
    monkeypatch.setattr(
        helpers,
        "ensure_global_counter",
        ensure_global_counter,
    )

    updated_user = await helpers.add_tries(
        session=session,
        user_or_id=user.id,
        count=5,
        paid=True,
    )

    assert updated_user is user
    assert user.tries_paid == 7
    assert user.tries_bonus == 3

    assert global_counter.paid_tries_total == 1005

    # Crediting attempts must not count them as played.
    assert game_state.paid_tries_this_cycle == 100
    assert game_state.lifetime_paid_tries == 500

    ensure_global_counter.assert_awaited_once_with(session)
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_add_bonus_tries_does_not_increment_paid_counters(monkeypatch):
    user = SimpleNamespace(
        id=10,
        tg_id=12345,
        tries_paid=2,
        tries_bonus=3,
    )

    result = SimpleNamespace(
        scalar_one_or_none=lambda: user
    )

    session = SimpleNamespace(
        execute=AsyncMock(return_value=result),
        add=lambda obj: None,
        flush=AsyncMock(),
    )

    ensure_global_counter = AsyncMock()
    monkeypatch.setattr(
        helpers,
        "ensure_global_counter",
        ensure_global_counter,
    )

    updated_user = await helpers.add_tries(
        session=session,
        user_or_id=user.id,
        count=4,
        paid=False,
    )

    assert updated_user is user
    assert user.tries_paid == 2
    assert user.tries_bonus == 7

    # Bonus attempts must not affect paid-attempt counters.
    ensure_global_counter.assert_not_awaited()
    session.flush.assert_awaited_once()


import services.playtrivia as playtrivia


@pytest.mark.asyncio
async def test_played_paid_attempt_increments_cycle_and_lifetime_once(monkeypatch):
    gs = SimpleNamespace(
        current_cycle=3,
        paid_tries_this_cycle=100,
        lifetime_paid_tries=500,
    )

    user = SimpleNamespace(
        id=10,
        tg_id=12345,
    )

    monkeypatch.setattr(
        playtrivia,
        "_ensure_game_state",
        AsyncMock(return_value=gs),
    )
    monkeypatch.setattr(
        playtrivia,
        "_ensure_cycle_row",
        AsyncMock(),
    )
    monkeypatch.setattr(
        playtrivia,
        "_record_play_safe",
        AsyncMock(),
    )
    monkeypatch.setattr(playtrivia, "WIN_THRESHOLD", 2000000)

    consume_try = AsyncMock(return_value="paid")

    outcome = await playtrivia.resolve_trivia_attempt(
        session=SimpleNamespace(),
        user=user,
        correct_answer=False,
        consume_try_fn=consume_try,
    )

    assert gs.paid_tries_this_cycle == 101
    assert gs.lifetime_paid_tries == 501
    assert outcome.paid_spin is True
    assert outcome.type == "lose"

    consume_try.assert_awaited_once()


@pytest.mark.asyncio
async def test_played_bonus_attempt_does_not_increment_paid_counters(monkeypatch):
    gs = SimpleNamespace(
        current_cycle=3,
        paid_tries_this_cycle=100,
        lifetime_paid_tries=500,
    )

    user = SimpleNamespace(
        id=10,
        tg_id=12345,
    )

    monkeypatch.setattr(
        playtrivia,
        "_ensure_game_state",
        AsyncMock(return_value=gs),
    )
    monkeypatch.setattr(
        playtrivia,
        "_ensure_cycle_row",
        AsyncMock(),
    )
    monkeypatch.setattr(
        playtrivia,
        "_record_play_safe",
        AsyncMock(),
    )
    monkeypatch.setattr(playtrivia, "WIN_THRESHOLD", 2000000)

    consume_try = AsyncMock(return_value="bonus")

    outcome = await playtrivia.resolve_trivia_attempt(
        session=SimpleNamespace(),
        user=user,
        correct_answer=False,
        consume_try_fn=consume_try,
    )

    assert gs.paid_tries_this_cycle == 100
    assert gs.lifetime_paid_tries == 500
    assert outcome.paid_spin is False
    assert outcome.type == "lose"

    consume_try.assert_awaited_once()
