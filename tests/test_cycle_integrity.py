from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import playtrivia


@pytest.mark.asyncio
async def test_end_cycle_starts_next_cycle_when_it_is_open(monkeypatch):
    session = SimpleNamespace(execute=AsyncMock())
    gs = SimpleNamespace(current_cycle=19, paid_tries_this_cycle=100)

    close_result = SimpleNamespace(rowcount=1)
    next_cycle_result = SimpleNamespace(
        first=lambda: SimpleNamespace(ended_at=None)
    )

    session.execute.side_effect = [
        close_result,
        next_cycle_result,
    ]

    ensure_row = AsyncMock()
    monkeypatch.setattr(playtrivia, "_ensure_cycle_row", ensure_row)

    result = await playtrivia._end_cycle_and_start_new(
        session=session,
        gs=gs,
        winner=None,
    )

    assert result["ended_cycle"] == 19
    assert result["new_cycle"] == 20
    assert gs.current_cycle == 20
    assert gs.paid_tries_this_cycle == 0
    ensure_row.assert_awaited_once_with(session, 20)


@pytest.mark.asyncio
async def test_end_cycle_rejects_closed_next_cycle(monkeypatch):
    session = SimpleNamespace(execute=AsyncMock())
    gs = SimpleNamespace(current_cycle=19, paid_tries_this_cycle=100)

    close_result = SimpleNamespace(rowcount=1)
    next_cycle_result = SimpleNamespace(
        first=lambda: SimpleNamespace(
            ended_at="2026-10-01T00:00:00+00:00"
        )
    )

    session.execute.side_effect = [
        close_result,
        next_cycle_result,
    ]

    ensure_row = AsyncMock()
    monkeypatch.setattr(playtrivia, "_ensure_cycle_row", ensure_row)

    with pytest.raises(RuntimeError, match="already closed"):
        await playtrivia._end_cycle_and_start_new(
            session=session,
            gs=gs,
            winner=None,
        )

    assert gs.current_cycle == 19
    assert gs.paid_tries_this_cycle == 100
    ensure_row.assert_awaited_once_with(session, 20)


@pytest.mark.asyncio
async def test_end_cycle_rejects_missing_next_cycle(monkeypatch):
    session = SimpleNamespace(execute=AsyncMock())
    gs = SimpleNamespace(current_cycle=19, paid_tries_this_cycle=100)

    close_result = SimpleNamespace(rowcount=1)
    next_cycle_result = SimpleNamespace(first=lambda: None)

    session.execute.side_effect = [
        close_result,
        next_cycle_result,
    ]

    ensure_row = AsyncMock()
    monkeypatch.setattr(playtrivia, "_ensure_cycle_row", ensure_row)

    with pytest.raises(RuntimeError, match="record does not exist"):
        await playtrivia._end_cycle_and_start_new(
            session=session,
            gs=gs,
            winner=None,
        )

    assert gs.current_cycle == 19
    assert gs.paid_tries_this_cycle == 100
    ensure_row.assert_awaited_once_with(session, 20)


@pytest.mark.asyncio
async def test_end_cycle_rejects_cycle_that_was_not_closed(monkeypatch):
    session = SimpleNamespace(execute=AsyncMock())
    gs = SimpleNamespace(current_cycle=19, paid_tries_this_cycle=100)

    session.execute.return_value = SimpleNamespace(rowcount=0)

    ensure_row = AsyncMock()
    monkeypatch.setattr(playtrivia, "_ensure_cycle_row", ensure_row)

    with pytest.raises(RuntimeError, match="Could not close exactly one active cycle"):
        await playtrivia._end_cycle_and_start_new(
            session=session,
            gs=gs,
            winner=None,
        )

    assert gs.current_cycle == 19
    assert gs.paid_tries_this_cycle == 100
    ensure_row.assert_not_awaited()