
import asyncio
import sys
from datetime import date
from types import ModuleType

from tasks import flutterwave_reconciliation as scheduler


class FakeBot:
    def __init__(self):
        self.sent_messages = []

    async def send_message(self, chat_id, text):
        self.sent_messages.append({
            "chat_id": chat_id,
            "text": text,
        })


def test_report_is_sent_only_to_admins_not_yet_notified(
    monkeypatch,
):
    fake_bot = FakeBot()

    fake_bot_module = ModuleType("bot_instance")
    fake_bot_module.bot = fake_bot

    monkeypatch.setitem(
        sys.modules,
        "bot_instance",
        fake_bot_module,
    )

    monkeypatch.setattr(
        scheduler,
        "get_admin_ids",
        lambda: {1001, 1002},
    )

    monkeypatch.setattr(
        scheduler,
        "build_reconciliation_report",
        lambda reporting_date, summary: "Test report",
    )

    saved_states = []

    async def fake_save_state(
        reporting_date,
        delivered_to,
        failed_for,
        status,
    ):
        saved_states.append({
            "delivered_to": set(delivered_to),
            "failed_for": set(failed_for),
            "status": status,
        })
        return True

    monkeypatch.setattr(
        scheduler,
        "save_admin_notification_state",
        fake_save_state,
    )

    summary = {
        "admin_notification_delivered_to": [1001],
        "admin_notification_failed_for": [],
    }

    result = asyncio.run(
        scheduler.deliver_saved_reconciliation_report(
            date(2026, 11, 17),
            summary,
        )
    )

    assert result is True

    # Admin 1001 already received the report.
    # Only Admin 1002 should receive another message.
    assert len(fake_bot.sent_messages) == 1
    assert fake_bot.sent_messages[0]["chat_id"] == 1002

    assert len(saved_states) == 1
    assert saved_states[0]["delivered_to"] == {
        1001,
        1002,
    }
    assert saved_states[0]["failed_for"] == set()
    assert saved_states[0]["status"] == "DELIVERED"
