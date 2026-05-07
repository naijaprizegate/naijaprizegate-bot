from telegram.ext import CommandHandler


async def university_handler(update, context):
    await update.effective_message.reply_text(
        "University section working."
    )


def register_handlers(app):
    app.add_handler(
        CommandHandler(
            "university",
            university_handler
        )
    )
