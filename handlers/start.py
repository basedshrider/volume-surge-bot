from telegram import Update
from telegram.ext import ContextTypes

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚀 <b>Volume Surge Bot Ready!</b>\n\n"
        "Talk naturally or use /menu\n\n"
        "Examples:\n"
        "• Track Solana and Base\n"
        "• Notify me when 5m volume exceeds 20%\n"
        "• Set minimum market cap to 500k",
        parse_mode="HTML"
    )
