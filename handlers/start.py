from telegram import Update
from telegram.ext import ContextTypes
from database.session import AsyncSessionLocal
from database.models import User
from sqlalchemy import select
from loguru import logger

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username

    async with AsyncSessionLocal() as db:
        existing = (await db.execute(select(User).where(User.user_id == user_id))).scalar_one_or_none()
        if not existing:
            new_user = User(user_id=user_id, username=username, settings={
                "is_paused": False,
                "volume_timeframe": "5m",
                "ratio_threshold": 10.0,
                "min_market_cap": 100000,
                "min_liquidity": 50000,
                "cooldown_minutes": 30,
                "selected_chains": ["solana", "base"]
            })
            db.add(new_user)
            await db.commit()
            logger.info(f"New user created: {user_id}")

    await update.message.reply_text(
        "🚀 <b>Volume Surge Bot is now running!</b>\n\n"
        "Talk to me naturally or use /menu\n\n"
        "Try: <code>Track Solana</code>",
        parse_mode="HTML"
    )
