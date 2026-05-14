import asyncio
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler
from config import config
from database.models import Base
from database.session import engine
from services.scanner import start_scanner
from handlers.start import start
from handlers.commands import show_menu, natural_language
from handlers.callbacks import callback_handler
from loguru import logger
import services.alert_engine

# Logging for Railway
logger.remove()
logger.add(lambda msg: print(msg, end=""), level="DEBUG", colorize=True)

async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    # Make bot available for alerts
    services.alert_engine.bot = app.bot

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", show_menu))
    
    # FIXED: correct filter syntax (no backslash)
    app.add_handler(MessageHandler(filters.TEXT & \~filters.COMMAND, natural_language))
    
    app.add_handler(CallbackQueryHandler(callback_handler))

    start_scanner()

    logger.success("🤖 Volume Surge Bot started successfully - no syntax error")
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
