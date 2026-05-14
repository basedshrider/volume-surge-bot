# ================================================
# VOLUME SURGE ALERT BOT - FINAL FIXED VERSION
# Fixed for Railway Free Plan + Python 3.9 + PTB v21
# May 14, 2026
# ================================================

import asyncio
import html
import re
from datetime import datetime
from typing import Dict, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackQueryHandler,
    ContextTypes,
)

from pydantic_settings import BaseSettings
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

# ====================== CONFIG ======================
class Settings(BaseSettings):
    TELEGRAM_BOT_TOKEN: str
    DATABASE_URL: str = "sqlite+aiosqlite:///bot.db"
    SCAN_INTERVAL_SECONDS: int = 60
    MAX_CONCURRENT_REQUESTS: int = 10

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

config = Settings()

# ====================== CONSTANTS ======================
CHAIN_MAP = {
    "solana": "Solana", "ethereum": "Ethereum", "base": "Base",
    "bsc": "BNB Chain", "arbitrum": "Arbitrum", "polygon": "Polygon",
    "avalanche": "Avalanche", "sui": "Sui", "tron": "Tron",
}

VOLUME_KEYS = {"5m": "m5", "1h": "h1", "6h": "h6", "24h": "h24"}

# ====================== DATABASE ======================
from sqlalchemy import Column, Integer, String, Float, JSON, DateTime, ForeignKey
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql import func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    user_id = Column(Integer, primary_key=True)
    username = Column(String, nullable=True)
    settings = Column(JSON, default=dict)
    created_at = Column(DateTime, server_default=func.now())

class Watchlist(Base):
    __tablename__ = "watchlist"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.user_id"))
    chain_id = Column(String, nullable=False)
    token_address = Column(String, nullable=False)
    symbol = Column(String)
    name = Column(String)

class AlertHistory(Base):
    __tablename__ = "alert_history"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer)
    token_address = Column(String)
    pair_address = Column(String)
    ratio = Column(Float)
    timestamp = Column(DateTime, server_default=func.now())

engine = create_async_engine(config.DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# ====================== DEX SCREENER ======================
import httpx

class DexScreenerClient:
    BASE_URL = "https://api.dexscreener.com"

    async def search(self, query: str):
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{self.BASE_URL}/latest/dex/search", params={"q": query})
            return resp.json().get("pairs", []) if resp.status_code == 200 else []

# ====================== HELPERS ======================
def calculate_volume_ratio(volume: float, mcap: Optional[float], fdv: Optional[float]) -> float:
    denominator = mcap if mcap and mcap > 0 else fdv
    if not denominator or denominator <= 0:
        return 0.0
    return (volume / denominator) * 100

# ====================== ALERT ENGINE ======================
async def send_alert(bot, user_id: int, pair: dict, ratio: float, timeframe: str):
    token = pair.get("baseToken", {})
    mcap = pair.get("marketCap") or pair.get("fdv") or 0
    liq = pair.get("liquidity", {}).get("usd", 0)
    volume = pair.get("volume", {}).get(VOLUME_KEYS.get(timeframe, "m5"), 0)

    text = f"""🚨 <b>Volume Surge Detected!</b>

<b>{html.escape(token.get('name',''))} ({token.get('symbol','')})</b>
Chain: {CHAIN_MAP.get(pair.get('chainId',''), pair.get('chainId',''))}
Price: ${pair.get('priceUsd','N/A')}
Market Cap: ${mcap:,.0f}
Liquidity: ${liq:,.0f}
{timeframe} Volume: ${volume:,.0f}
Ratio: <b>{ratio:.1f}%</b>

<a href="{pair.get('url','')}">📊 View on DEX Screener</a>"""

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Open Chart", url=pair.get('url',''))],
        [
            InlineKeyboardButton("⏰ Snooze 1h", callback_data=f"snooze_{pair.get('pairAddress','')}"),
            InlineKeyboardButton("🚫 Ignore Token", callback_data=f"ignore_{token.get('address','')}")
        ]
    ])

    try:
        await bot.send_message(chat_id=user_id, text=text, parse_mode="HTML", reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Alert failed: {e}")

# ====================== COMMAND PARSER ======================
def parse_natural_language(text: str) -> Dict:
    text = text.lower().strip()
    if re.search(r"(track|monitor|watch|follow)", text):
        return {"action": "add_chains"}
    if re.search(r"(\d+)%", text):
        return {"action": "set_threshold"}
    if "pause" in text or "stop" in text:
        return {"action": "pause"}
    return {"action": "unknown"}

# ====================== HANDLERS ======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚀 <b>Volume Surge Bot is ONLINE!</b>\n\n"
        "Scanner is currently disabled.\n"
        "Type /menu or talk naturally.",
        parse_mode="HTML"
    )

async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("📊 Menu", callback_data="menu")]]
    await update.message.reply_text("Main Menu", reply_markup=InlineKeyboardMarkup(keyboard))

async def natural_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    intent = parse_natural_language(update.message.text)
    await update.message.reply_text(f"✅ Understood: {intent['action']}")

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("Action received")

# ====================== POST INIT ======================
async def post_init(application: Application):
    await init_db()
    logger.success("✅ Database initialized successfully")

# ====================== MAIN ======================
def main():
    app = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", show_menu))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, natural_language))
    app.add_handler(CallbackQueryHandler(callback_handler))

    logger.success("🤖 Volume Surge Bot started successfully (scanner disabled)")
    app.run_polling()   # ← This is synchronous - do NOT await it

if __name__ == "__main__":
    logger.remove()
    logger.add(lambda msg: print(msg, end=""), level="DEBUG", colorize=True)
    main()
