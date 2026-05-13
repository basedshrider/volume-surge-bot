from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("▶️ Start Monitoring", callback_data="start_monitoring")],
        [InlineKeyboardButton("⏸️ Pause", callback_data="pause")],
        [InlineKeyboardButton("⚙️ Configure Filters", callback_data="config_filters")],
        [InlineKeyboardButton("🌐 Select Chains", callback_data="select_chains")],
        [InlineKeyboardButton("📊 Status", callback_data="status")],
    ]
    await update.message.reply_text("Main Menu", reply_markup=InlineKeyboardMarkup(keyboard))

async def natural_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from services.command_parser import parse_natural_language
    intent = parse_natural_language(update.message.text)
    await update.message.reply_text(f"✅ Understood: {intent.get('action', 'unknown')}")
