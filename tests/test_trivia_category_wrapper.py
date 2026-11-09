import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://test:test@localhost:5432/test_db",
)
os.environ.setdefault(
    "WINNER_SIGNING_KEY",
    "test-only-signing-key-for-local-tests",
)
os.environ.setdefault(
    "FLW_SECRET_KEY",
    "FLWSECK_TEST-dummy-local-test-key",
)

from handlers import playtrivia


@pytest.mark.asyncio
async def test_category_wrapper_releases_lock_on_success(monkeypatch):
    user_data = {
        "trivia_category_selection_lock": True,
    }
    context = SimpleNamespace(user_data=user_data)
    update = SimpleNamespace()

    inner_handler = AsyncMock()
    monkeypatch.setattr(
        playtrivia,
        "_trivia_category_handler_inner",
        inner_handler,
    )

    await playtrivia.trivia_category_handler(update, context)

    inner_handler.assert_awaited_once_with(update, context)
    assert user_data["trivia_category_selection_lock"] is False


@pytest.mark.asyncio
async def test_category_wrapper_cleans_up_and_releases_lock_on_error(monkeypatch):
    timer = asyncio.create_task(asyncio.sleep(60))

    user_data = {
        "trivia_category_selection_lock": True,
        "trivia_timer": timer,
        "pending_trivia_question": {"id": 123},
        "pending_trivia_category": "History",
        "pending_trivia_category_key": "nigeria_history",
        "trivia_deadline": 123456789,
        "trivia_answered": True,
        "trivia_processing_lock": True,
    }

    context = SimpleNamespace(user_data=user_data)
    update = SimpleNamespace()

    inner_handler = AsyncMock(
        side_effect=RuntimeError("Question setup failed")
    )
    monkeypatch.setattr(
        playtrivia,
        "_trivia_category_handler_inner",
        inner_handler,
    )

    with pytest.raises(RuntimeError, match="Question setup failed"):
        await playtrivia.trivia_category_handler(update, context)

    await asyncio.gather(timer, return_exceptions=True)

    assert timer.cancelled()
    assert "trivia_timer" not in user_data
    assert "pending_trivia_question" not in user_data
    assert "pending_trivia_category" not in user_data
    assert "pending_trivia_category_key" not in user_data
    assert "trivia_deadline" not in user_data
    assert user_data["trivia_answered"] is False
    assert user_data["trivia_processing_lock"] is False
    assert user_data["trivia_category_selection_lock"] is False


@pytest.mark.asyncio
async def test_reward_wrapper_cleans_up_after_success(monkeypatch):
    user_data = {
        "pending_trivia_question": {"id": 123},
        "pending_trivia_category": "History",
        "pending_trivia_category_key": "nigeria_history",
        "trivia_deadline": 123456789,
        "trivia_question_id": "123",
        "trivia_answered": True,
        "trivia_processing_lock": True,
        "trivia_category_selection_lock": True,
    }

    context = SimpleNamespace(user_data=user_data)
    update = SimpleNamespace()

    inner = AsyncMock()
    monkeypatch.setattr(
        playtrivia,
        "_run_spin_and_apply_reward_inner",
        inner,
    )

    await playtrivia.run_spin_and_apply_reward(update, context)

    inner.assert_awaited_once_with(
        update,
        context,
        trivia_message_id=None,
    )

    assert "pending_trivia_question" not in user_data
    assert "pending_trivia_category" not in user_data
    assert "pending_trivia_category_key" not in user_data
    assert "trivia_deadline" not in user_data
    assert "trivia_question_id" not in user_data
    assert user_data["trivia_answered"] is False
    assert user_data["trivia_processing_lock"] is False
    assert user_data["trivia_category_selection_lock"] is False


@pytest.mark.asyncio
async def test_reward_wrapper_cleans_up_when_inner_handler_fails(monkeypatch):
    user_data = {
        "pending_trivia_question": {"id": 123},
        "pending_trivia_category": "History",
        "pending_trivia_category_key": "nigeria_history",
        "trivia_deadline": 123456789,
        "trivia_question_id": "123",
        "trivia_answered": True,
        "trivia_processing_lock": True,
        "trivia_category_selection_lock": True,
    }

    context = SimpleNamespace(user_data=user_data)
    update = SimpleNamespace()

    inner = AsyncMock(
        side_effect=RuntimeError("Reward processing failed")
    )
    monkeypatch.setattr(
        playtrivia,
        "_run_spin_and_apply_reward_inner",
        inner,
    )

    with pytest.raises(RuntimeError, match="Reward processing failed"):
        await playtrivia.run_spin_and_apply_reward(update, context)

    assert "pending_trivia_question" not in user_data
    assert "pending_trivia_category" not in user_data
    assert "pending_trivia_category_key" not in user_data
    assert "trivia_deadline" not in user_data
    assert "trivia_question_id" not in user_data
    assert user_data["trivia_answered"] is False
    assert user_data["trivia_processing_lock"] is False
    assert user_data["trivia_category_selection_lock"] is False


@pytest.mark.asyncio
async def test_answer_callback_failure_resets_locks_and_preserves_timer():
    timer = asyncio.create_task(asyncio.sleep(60))

    user_data = {
        "pending_trivia_question": {
            "id": 123,
            "answer": "A",
            "options": {
                "A": "Correct",
                "B": "Wrong 1",
                "C": "Wrong 2",
                "D": "Wrong 3",
            },
        },
        "trivia_answered": False,
        "trivia_processing_lock": False,
        "trivia_timer": timer,
    }

    query = SimpleNamespace(
        data="ans_123_A",
        answer=AsyncMock(
            side_effect=RuntimeError("Telegram acknowledgement failed")
        ),
    )

    update = SimpleNamespace(callback_query=query)
    context = SimpleNamespace(user_data=user_data)

    try:
        with pytest.raises(
            RuntimeError,
            match="Telegram acknowledgement failed",
        ):
            await playtrivia.trivia_answer_handler(update, context)

        assert user_data["trivia_processing_lock"] is False
        assert user_data["trivia_answered"] is False
        assert user_data["trivia_timer"] is timer
        assert not timer.cancelled()

        query.answer.assert_awaited_once()

    finally:
        timer.cancel()
        await asyncio.gather(timer, return_exceptions=True)
