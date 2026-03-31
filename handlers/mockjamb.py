# ====================================================================
# handlers/mockjamb.py
# ====================================================================

import math
import logging
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from jamb_loader import get_course_subject_map, get_course_by_code, get_course_subjects


logger = logging.getLogger(__name__)

COURSES_PER_PAGE = 6


# ====================================================================
# Keyboards
# ====================================================================
def make_mockjamb_welcome_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🎯 Choose Course", callback_data="mj_course_page_1")],
            [InlineKeyboardButton("⬅️ Back to Exam Hub", callback_data="exam:hub")],
            [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
        ]
    )


# ====================================================================
# Message Builders
# ====================================================================
def build_mockjamb_welcome_text() -> str:
    return (
        "📝 *Welcome to Mock JAMB / UTME*\n\n"
        "This mock exam is designed to simulate the real UTME experience.\n\n"
        "You will write *4 subjects*:\n"
        "• *Use of English* \\(compulsory\\)\n"
        "• *3 other subjects* based on your intended course\n\n"
        "To begin, choose your intended course and we will recommend a likely JAMB subject combination for you."
    )


# ====================================================================
# Entry Handler
# ====================================================================
async def mockjamb_start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        query = update.callback_query
        await query.answer()

        context.user_data["mj_course_code"] = None
        context.user_data["mj_subject_codes"] = []
        context.user_data["mj_mode"] = None
        context.user_data["mj_room_code"] = None

        try:
            await query.edit_message_text(
                build_mockjamb_welcome_text(),
                parse_mode="Markdown",
                reply_markup=make_mockjamb_welcome_keyboard(),
            )
        except Exception:
            await query.message.reply_text(
                build_mockjamb_welcome_text(),
                parse_mode="Markdown",
                reply_markup=make_mockjamb_welcome_keyboard(),
            )
        return

    if update.message:
        context.user_data["mj_course_code"] = None
        context.user_data["mj_subject_codes"] = []
        context.user_data["mj_mode"] = None
        context.user_data["mj_room_code"] = None

        await update.message.reply_text(
            build_mockjamb_welcome_text(),
            parse_mode="Markdown",
            reply_markup=make_mockjamb_welcome_keyboard(),
        )


# ====================================================================
# Register Handlers
# ====================================================================
def register_handlers(application):
    application.add_handler(CommandHandler("mockjamb", mockjamb_start_handler))
    application.add_handler(CallbackQueryHandler(mockjamb_start_handler, pattern=r"^mock:jamb$"))

