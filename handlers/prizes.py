# ---------------------------------------------
# handlers/prizes.py
# ---------------------------------------------
"""Prize catalogue handlers for NaijaPrizeGate.

This module manages the display and administration of prizes.
It does not award prizes or modify payment/trivia logic.
"""

import html
import logging
from uuid import UUID

from sqlalchemy import select
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Update,
)
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from db import get_async_session
from handlers.admin import is_admin
from models import PrizeCatalogue

logger = logging.getLogger(__name__)

# Conversation states for adding and editing prizes.
NAME, DESCRIPTION, CONDITIONS, PHOTO, EDIT_VALUE = range(5)

PUBLIC_OPEN = "prizes:open"
PUBLIC_PAGE = "prizes:page:"
PUBLIC_MAIN_MENU = "prizes:main_menu"

ADMIN_OPEN = "prize_admin:open"
ADMIN_LIST = "prize_admin:list"
ADMIN_ADD = "prize_admin:add"
ADMIN_EDIT = "prize_admin:edit:"
ADMIN_TOGGLE = "prize_admin:toggle:"
ADMIN_DELETE = "prize_admin:delete:"
ADMIN_DELETE_CONFIRM = "prize_admin:delete_confirm"
ADMIN_BACK = "prize_admin:back"
ADMIN_CANCEL = "prize_admin:cancel"


def _authorized(update: Update) -> bool:
    user = update.effective_user
    return bool(user and is_admin(user.id))


def _escape(value):
    return html.escape(value or "")


def _main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎮 Entertainment",
                              callback_data="menu:entertainment")],
        [InlineKeyboardButton("🎓 Education",
                              callback_data="menu:education")],
        [InlineKeyboardButton("💰 Finance (Wallet & Rewards)",
                              callback_data="finance:open")],
        [InlineKeyboardButton("🏆 View Prizes",
                              callback_data=PUBLIC_OPEN)],
    ])


def _admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Prize", callback_data=ADMIN_ADD)],
        [InlineKeyboardButton("📋 View / Edit Prizes",
                              callback_data=ADMIN_LIST)],
        [InlineKeyboardButton("🏠 Admin Panel",
                              callback_data="admin_menu:main")],
    ])


async def _get_prizes(active_only=False):
    async with get_async_session() as session:
        statement = select(PrizeCatalogue)

        if active_only:
            statement = statement.where(
                PrizeCatalogue.is_active.is_(True)
            )

        statement = statement.order_by(
            PrizeCatalogue.created_at.asc(),
            PrizeCatalogue.id.asc(),
        )

        result = await session.execute(statement)
        return list(result.scalars().all())


async def _get_prize(prize_id):
    try:
        parsed_id = UUID(prize_id)
    except (ValueError, TypeError, AttributeError):
        return None

    async with get_async_session() as session:
        return await session.get(PrizeCatalogue, parsed_id)


def _public_caption(prize, index, total):
    parts = [f"<b>{_escape(prize.name)}</b>"]

    if prize.description:
        parts.append(_escape(prize.description))

    if prize.conditions:
        parts.append(
            f"<b>Conditions:</b>\n{_escape(prize.conditions)}"
        )

    parts.append(f"<i>Prize {index + 1} of {total}</i>")
    return "\n\n".join(parts)


def _public_keyboard(index, total):
    rows = []
    navigation = []

    if index > 0:
        navigation.append(
            InlineKeyboardButton(
                "⬅️ Previous",
                callback_data=f"{PUBLIC_PAGE}{index - 1}",
            )
        )

    if index < total - 1:
        navigation.append(
            InlineKeyboardButton(
                "Next ➡️",
                callback_data=f"{PUBLIC_PAGE}{index + 1}",
            )
        )

    if navigation:
        rows.append(navigation)

    rows.append([
        InlineKeyboardButton(
            "🏠 Main Menu",
            callback_data=PUBLIC_MAIN_MENU,
        )
    ])

    return InlineKeyboardMarkup(rows)


async def _show_public_prize(update, index, edit=False):
    query = update.callback_query
    prizes = await _get_prizes(active_only=True)

    if not prizes:
        text = (
            "?? <b>Prize Catalogue</b>\n\n"
            "There are no prizes available at the moment. "
            "Please check back later."
        )
        if query and edit:
            try:
                await query.edit_message_text(
                    text,
                    parse_mode="HTML",
                    reply_markup=_main_menu_keyboard(),
                )
                return
            except Exception:
                logger.info("Could not edit empty catalogue message")

        message = query.message if query else update.effective_message
        if message:
            if query and edit:
                try:
                    await message.delete()
                except Exception:
                    logger.info("Could not delete old catalogue message")
            await message.reply_text(
                text,
                parse_mode="HTML",
                reply_markup=_main_menu_keyboard(),
            )
        return

    index = max(0, min(index, len(prizes) - 1))
    prize = prizes[index]
    caption = _public_caption(prize, index, len(prizes))
    keyboard = _public_keyboard(index, len(prizes))

    if query and edit:
        try:
            if prize.photo_file_id:
                await query.edit_message_media(
                    media=InputMediaPhoto(
                        media=prize.photo_file_id,
                        caption=caption,
                        parse_mode="HTML",
                    ),
                    reply_markup=keyboard,
                )
            else:
                await query.edit_message_text(
                    caption,
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
            return
        except Exception:
            logger.info(
                "Could not edit catalogue message; replacing it"
            )

        old_message = query.message
        try:
            await old_message.delete()
        except Exception:
            logger.info("Could not delete old catalogue message")

        if prize.photo_file_id:
            await old_message.chat.send_photo(
                photo=prize.photo_file_id,
                caption=caption,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        else:
            await old_message.chat.send_message(
                text=caption,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        return

    message = query.message if query else update.effective_message
    if not message:
        return

    if prize.photo_file_id:
        await message.reply_photo(
            photo=prize.photo_file_id,
            caption=caption,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
    else:
        await message.reply_text(
            caption,
            parse_mode="HTML",
            reply_markup=keyboard,
        )


async def public_prizes_open(update, context):
    query = update.callback_query
    if query:
        await query.answer()

    await _show_public_prize(update, 0)


async def public_prizes_page(update, context):
    query = update.callback_query

    if not query:
        return

    await query.answer()

    try:
        index = int(query.data.rsplit(":", 1)[1])
    except (ValueError, IndexError):
        index = 0

    await _show_public_prize(update, index, edit=True)


async def _edit_or_replace_text_message(query, text, reply_markup=None):
    """Edit a navigation message, or replace it if Telegram cannot edit it."""
    try:
        await query.edit_message_text(
            text,
            reply_markup=reply_markup,
        )
        return
    except Exception:
        logger.info(
            "Could not edit navigation message; attempting replacement",
            exc_info=True,
        )

    message = query.message
    if not message:
        return

    try:
        await message.delete()
    except Exception:
        logger.info(
            "Could not delete old navigation message",
            exc_info=True,
        )

    try:
        await message.chat.send_message(
            text=text,
            reply_markup=reply_markup,
        )
    except Exception:
        logger.exception("Could not send replacement navigation message")


async def public_prizes_main_menu(update, context):
    query = update.callback_query

    if not query:
        return

    await query.answer()

    await _edit_or_replace_text_message(
        query,
        "Choose an option from the main menu.",
        reply_markup=_main_menu_keyboard(),
    )

async def admin_prizes_open(update, context):
    query = update.callback_query

    if not _authorized(update):
        if query:
            await query.answer(
                "You are not authorized to manage prizes.",
                show_alert=True,
            )
        return

    if query:
        await query.answer()
        await _edit_or_replace_text_message(
            query,
            "\U0001F3C6 Prize Management\n\nChoose an action:",
            reply_markup=_admin_keyboard(),
        )

async def admin_prizes_list(update, context):
    query = update.callback_query

    if not _authorized(update):
        if query:
            await query.answer(
                "You are not authorized to manage prizes.",
                show_alert=True,
            )
        return

    if not query:
        return

    await query.answer()
    prizes = await _get_prizes()

    rows = []

    for prize in prizes:
        status = "🟢" if prize.is_active else "⚪"
        rows.append([
            InlineKeyboardButton(
                f"{status} {prize.name[:45]}",
                callback_data=f"{ADMIN_EDIT}{prize.id}",
            )
        ])

    rows.append([
        InlineKeyboardButton("➕ Add Prize", callback_data=ADMIN_ADD)
    ])
    rows.append([
        InlineKeyboardButton("⬅️ Back", callback_data=ADMIN_OPEN)
    ])

    text = (
        "No prizes have been added yet."
        if not prizes
        else "Select a prize to view or edit it:"
    )

    await _edit_or_replace_text_message(
        query,
        text,
        reply_markup=InlineKeyboardMarkup(rows),
    )


def _prize_keyboard(prize):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "✏️ Edit Name",
            callback_data=f"prize_admin:field:name:{prize.id}",
        )],
        [InlineKeyboardButton(
            "✏️ Edit Description",
            callback_data=f"prize_admin:field:description:{prize.id}",
        )],
        [InlineKeyboardButton(
            "✏️ Edit Conditions",
            callback_data=f"prize_admin:field:conditions:{prize.id}",
        )],
        [InlineKeyboardButton(
            "🖼 Replace Picture",
            callback_data=f"prize_admin:field:photo:{prize.id}",
        )],
        [InlineKeyboardButton(
            "🔄 Activate / Deactivate",
            callback_data=f"{ADMIN_TOGGLE}{prize.id}",
        )],
        [InlineKeyboardButton(
            "🗑 Delete Prize",
            callback_data=f"{ADMIN_DELETE}{prize.id}",
        )],
        [InlineKeyboardButton(
            "⬅️ Prize List",
            callback_data=ADMIN_LIST,
        )],
    ])


async def admin_prize_edit(update, context):
    query = update.callback_query

    if not _authorized(update):
        if query:
            await query.answer(
                "You are not authorized to manage prizes.",
                show_alert=True,
            )
        return

    if not query:
        return

    # Toggle callbacks have already been acknowledged by admin_prize_toggle.
    if not query.data.startswith(ADMIN_TOGGLE):
        await query.answer()

    if query.data.startswith(ADMIN_TOGGLE):
        prize_id = query.data[len(ADMIN_TOGGLE):]
    else:
        prize_id = query.data[len(ADMIN_EDIT):]

    prize = await _get_prize(prize_id)

    if not prize:
        try:
            await query.edit_message_text(
                "That prize could not be found.",
                reply_markup=_admin_keyboard(),
            )
        except Exception:
            logger.info("Could not edit missing-prize message")
        return

    context.user_data["prize_admin_selected_id"] = str(prize.id)

    text = (
        f"<b>{_escape(prize.name)}</b>\n\n"
        f"<b>Description:</b> "
        f"{_escape(prize.description) or '(empty)'}\n\n"
        f"<b>Conditions:</b> "
        f"{_escape(prize.conditions) or '(empty)'}\n\n"
        f"<b>Status:</b> "
        f"{'Active' if prize.is_active else 'Inactive'}\n"
        f"<b>Picture:</b> "
        f"{'Set' if prize.photo_file_id else 'Not set'}"
    )
    keyboard = _prize_keyboard(prize)

    if prize.photo_file_id:
        try:
            await query.edit_message_media(
                media=InputMediaPhoto(
                    media=prize.photo_file_id,
                    caption=text,
                    parse_mode="HTML",
                ),
                reply_markup=keyboard,
            )
            return
        except Exception:
            logger.info(
                "Could not edit prize message to show photo; replacing it"
            )
    else:
        try:
            await query.edit_message_text(
                text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            return
        except Exception:
            logger.info(
                "Could not edit prize message as text; replacing it"
            )

    old_message = query.message
    try:
        await old_message.delete()
    except Exception:
        logger.info("Could not delete old admin prize message")

    if prize.photo_file_id:
        await old_message.chat.send_photo(
            photo=prize.photo_file_id,
            caption=text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
    else:
        await old_message.chat.send_message(
            text=text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )


async def admin_prize_toggle(update, context):
    query = update.callback_query

    if not _authorized(update):
        if query:
            await query.answer(
                "You are not authorized to manage prizes.",
                show_alert=True,
            )
        return

    if not query:
        return

    await query.answer()
    prize_id = query.data[len(ADMIN_TOGGLE):]

    try:
        parsed_id = UUID(prize_id)
    except ValueError:
        await query.edit_message_text("Invalid prize ID.")
        return

    async with get_async_session() as session:
        prize = await session.get(PrizeCatalogue, parsed_id)

        if not prize:
            await query.edit_message_text("Prize not found.")
            return

        prize.is_active = not prize.is_active
        await session.commit()

    await admin_prize_edit(update, context)


async def admin_prize_delete(update, context):
    query = update.callback_query

    if not _authorized(update):
        if query:
            await query.answer(
                "You are not authorized to manage prizes.",
                show_alert=True,
            )
        return

    if not query:
        return

    await query.answer()
    prize_id = query.data[len(ADMIN_DELETE):]

    if not await _get_prize(prize_id):
        await query.edit_message_text("Prize not found.")
        return

    context.user_data["prize_admin_delete_id"] = prize_id

    await query.edit_message_text(
        "Are you sure you want to delete this prize?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "🗑 Yes, delete",
                callback_data=ADMIN_DELETE_CONFIRM,
            )],
            [InlineKeyboardButton(
                "Cancel",
                callback_data=ADMIN_BACK,
            )],
        ]),
    )


async def admin_prize_delete_confirm(update, context):
    query = update.callback_query

    if not _authorized(update):
        if query:
            await query.answer(
                "You are not authorized to manage prizes.",
                show_alert=True,
            )
        return

    if not query:
        return

    await query.answer()
    prize_id = context.user_data.pop("prize_admin_delete_id", None)

    if not prize_id:
        await query.edit_message_text(
            "Deletion expired. Please open prize management again.",
            reply_markup=_admin_keyboard(),
        )
        return

    try:
        parsed_id = UUID(prize_id)
    except ValueError:
        await query.edit_message_text("Invalid prize ID.")
        return

    async with get_async_session() as session:
        prize = await session.get(PrizeCatalogue, parsed_id)

        if not prize:
            await query.edit_message_text("Prize no longer exists.")
            return

        await session.delete(prize)
        await session.commit()

    await query.edit_message_text(
        "✅ Prize deleted.",
        reply_markup=_admin_keyboard(),
    )


async def start_add_prize(update, context):
    query = update.callback_query

    if not _authorized(update):
        if query:
            await query.answer(
                "You are not authorized to manage prizes.",
                show_alert=True,
            )
        return ConversationHandler.END

    if query:
        await query.answer()
        await query.edit_message_text(
            "Enter the prize name. Send /cancel to stop."
        )

    context.user_data["prize_admin_draft"] = {}
    return NAME


async def receive_name(update, context):
    if not _authorized(update):
        return ConversationHandler.END

    name = (update.effective_message.text or "").strip()

    if not name or name.startswith("/"):
        await update.effective_message.reply_text(
            "Please enter a valid prize name."
        )
        return NAME

    context.user_data["prize_admin_draft"]["name"] = name[:200]

    await update.effective_message.reply_text(
        "Enter the public description, or send /skip."
    )
    return DESCRIPTION


async def receive_description(update, context):
    if not _authorized(update):
        return ConversationHandler.END

    text = (update.effective_message.text or "").strip()

    context.user_data["prize_admin_draft"]["description"] = (
        "" if text == "/skip" else text[:4000]
    )

    await update.effective_message.reply_text(
        "Enter the conditions, or send /skip."
    )
    return CONDITIONS


async def receive_conditions(update, context):
    if not _authorized(update):
        return ConversationHandler.END

    text = (update.effective_message.text or "").strip()

    context.user_data["prize_admin_draft"]["conditions"] = (
        "" if text == "/skip" else text[:2000]
    )

    await update.effective_message.reply_text(
        "Send a prize photo, or send /skip to continue without one."
    )
    return PHOTO


async def receive_photo(update, context):
    if not _authorized(update):
        return ConversationHandler.END

    message = update.effective_message
    photo_file_id = None

    if message.photo:
        photo_file_id = message.photo[-1].file_id
    elif (message.text or "").strip() != "/skip":
        await message.reply_text(
            "Please send a photo or send /skip."
        )
        return PHOTO

    draft = context.user_data.pop("prize_admin_draft", {})
    name = draft.get("name", "").strip()

    if not name:
        await message.reply_text("Prize name is missing. Please start again.")
        return ConversationHandler.END

    async with get_async_session() as session:
        prize = PrizeCatalogue(
            name=name,
            description=draft.get("description", ""),
            conditions=draft.get("conditions", ""),
            photo_file_id=photo_file_id,
            is_active=True,
        )
        session.add(prize)
        await session.commit()

    await message.reply_text(
        "✅ Prize added successfully.",
        reply_markup=_admin_keyboard(),
    )
    return ConversationHandler.END


async def start_edit_field(update, context):
    query = update.callback_query

    if not _authorized(update):
        if query:
            await query.answer(
                "You are not authorized to manage prizes.",
                show_alert=True,
            )
        return ConversationHandler.END

    if not query:
        return ConversationHandler.END

    await query.answer()
    parts = query.data.split(":")

    field = parts[2]
    prize_id = parts[3]

    if field not in {"name", "description", "conditions", "photo"}:
        await query.edit_message_text("Invalid field.")
        return ConversationHandler.END

    if not await _get_prize(prize_id):
        await query.edit_message_text("Prize not found.")
        return ConversationHandler.END

    context.user_data["prize_admin_edit"] = {
        "id": prize_id,
        "field": field,
    }

    prompts = {
        "name": "Send the new prize name.",
        "description": "Send the new description, or /clear.",
        "conditions": "Send the new conditions, or /clear.",
        "photo": "Send the new prize photo.",
    }

    await query.edit_message_text(
        prompts[field] + " Send /cancel to stop."
    )
    return EDIT_VALUE


async def receive_edit_value(update, context):
    if not _authorized(update):
        return ConversationHandler.END

    message = update.effective_message
    edit = context.user_data.get("prize_admin_edit", {})
    field = edit.get("field")
    prize_id = edit.get("id")

    if not message or not field or not prize_id:
        return ConversationHandler.END

    if field == "photo":
        if not message.photo:
            await message.reply_text("Please send a photo or /cancel.")
            return EDIT_VALUE
        value = message.photo[-1].file_id
    else:
        value = (message.text or "").strip()

        if field == "name" and (not value or value.startswith("/")):
            await message.reply_text("Please enter a valid name.")
            return EDIT_VALUE

        if value == "/clear" and field in {"description", "conditions"}:
            value = ""
        elif value.startswith("/"):
            await message.reply_text(
                "Please send the requested text, or use /clear where allowed."
            )
            return EDIT_VALUE

        limit = {
            "name": 200,
            "description": 4000,
            "conditions": 2000,
        }[field]
        value = value[:limit]

    try:
        parsed_id = UUID(prize_id)
    except ValueError:
        await message.reply_text("Invalid prize ID.")
        return ConversationHandler.END

    async with get_async_session() as session:
        prize = await session.get(PrizeCatalogue, parsed_id)

        if not prize:
            await message.reply_text("Prize not found.")
            return ConversationHandler.END

        if field == "name":
            prize.name = value
        elif field == "description":
            prize.description = value
        elif field == "conditions":
            prize.conditions = value
        elif field == "photo":
            prize.photo_file_id = value

        await session.commit()

    context.user_data.pop("prize_admin_edit", None)

    await message.reply_text(
        "✅ Prize updated.",
        reply_markup=_admin_keyboard(),
    )
    return ConversationHandler.END


async def cancel_prize_operation(update, context):
    if not _authorized(update):
        return ConversationHandler.END

    context.user_data.pop("prize_admin_draft", None)
    context.user_data.pop("prize_admin_edit", None)

    await update.effective_message.reply_text(
        "Prize operation cancelled.",
        reply_markup=_admin_keyboard(),
    )
    return ConversationHandler.END


async def cancel_prize_button(update, context):
    query = update.callback_query

    if not _authorized(update):
        if query:
            await query.answer(
                "You are not authorized to manage prizes.",
                show_alert=True,
            )
        return ConversationHandler.END

    if query:
        await query.answer()
        await query.edit_message_text(
            "Prize operation cancelled.",
            reply_markup=_admin_keyboard(),
        )

    context.user_data.pop("prize_admin_draft", None)
    context.user_data.pop("prize_admin_edit", None)
    return ConversationHandler.END


async def back_to_admin(update, context):
    query = update.callback_query

    if not _authorized(update):
        if query:
            await query.answer(
                "You are not authorized to manage prizes.",
                show_alert=True,
            )
        return

    if query:
        await query.answer()

        context.user_data.pop("prize_admin_delete_id", None)
        await _edit_or_replace_text_message(
            query,
            "\U0001F3C6 Prize Management\n\nChoose an action:",
            reply_markup=_admin_keyboard(),
        )

def register_handlers(application):
    """Register public prize handlers and admin management handlers."""

    application.add_handler(
        CallbackQueryHandler(public_prizes_open, pattern=r"^prizes:open$")
    )
    application.add_handler(
        CallbackQueryHandler(
            public_prizes_page,
            pattern=r"^prizes:page:\d+$",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            public_prizes_main_menu,
            pattern=r"^prizes:main_menu$",
        )
    )

    # Register the conversation in group 9, before broad admin text handlers.
    conversation = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                start_add_prize,
                pattern=r"^prize_admin:add$",
            ),
            CallbackQueryHandler(
                start_edit_field,
                pattern=r"^prize_admin:field:(name|description|conditions|photo):[0-9a-fA-F-]{36}$",
            ),
        ],
        states={
            NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_name)
            ],
            DESCRIPTION: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_description,
                ),
                CommandHandler("skip", receive_description),
            ],
            CONDITIONS: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_conditions,
                ),
                CommandHandler("skip", receive_conditions),
            ],
            PHOTO: [
                MessageHandler(filters.PHOTO, receive_photo),
                CommandHandler("skip", receive_photo),
            ],
            EDIT_VALUE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_edit_value,
                ),
                MessageHandler(filters.PHOTO, receive_edit_value),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_prize_operation),
            CallbackQueryHandler(
                cancel_prize_button,
                pattern=r"^prize_admin:cancel$",
            ),
        ],
        name="prize_catalogue_conversation",
        persistent=False,
    )
    application.add_handler(conversation, group=9)

    application.add_handler(
        CallbackQueryHandler(admin_prizes_open, pattern=r"^prize_admin:open$")
    )
    application.add_handler(
        CallbackQueryHandler(admin_prizes_list, pattern=r"^prize_admin:list$")
    )
    application.add_handler(
        CallbackQueryHandler(
            admin_prize_edit,
            pattern=r"^prize_admin:edit:[0-9a-fA-F-]{36}$",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            admin_prize_toggle,
            pattern=r"^prize_admin:toggle:[0-9a-fA-F-]{36}$",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            admin_prize_delete,
            pattern=r"^prize_admin:delete:[0-9a-fA-F-]{36}$",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            admin_prize_delete_confirm,
            pattern=r"^prize_admin:delete_confirm$",
        )
    )
    application.add_handler(
        CallbackQueryHandler(back_to_admin, pattern=r"^prize_admin:back$")
    )
