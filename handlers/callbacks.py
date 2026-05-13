from telegram import Update
from telegram.ext import ContextTypes

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Action received")
