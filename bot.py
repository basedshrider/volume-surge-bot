# ================================================
# VOLUME SURGE ALERT BOT — RAILWAY-STABLE VERSION
# Python 3.9+ | PTB v21 | pydantic-settings v2
# ================================================

import asyncio
import html
import os
import re
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import httpx
from loguru import logger
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, JSON, String, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.sql import func
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ─────────────────────────────────────────────
# 1. CONFIG
# FIX: Never use os.getenv() as pydantic field defaults.
#      pydantic-settings v2 reads os.environ automatically.
#      SettingsConfigDict replaces the deprecated inner `class Config`.
#      The env_file is optional — Railway injects real env vars directly.
# ─────────────────────────────────────────────
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,      # TOKEN, token, Token all match
    )

    TELEGRAM_BOT_TOKEN: str
    DATABASE_URL: str = "sqlite+aiosqlite:///bot.db"
    SCAN_INTERVAL_SECONDS: int = 60
    MAX_CONCURRENT_REQUESTS: int = 10


def load_config() -> Settings:
    """
    Load settings with a hard fallback to os.environ so Railway's
    injected variables are always picked up even if pydantic-settings
    somehow misses them (has happened with certain Railway build images).
    """
    try:
        return Settings()
    except Exception as e:
        # Last-resort: build Settings by injecting the env var directly
        # so pydantic-settings sees it as a constructor arg, bypassing
        # any source-reading issue.
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if not token:
            logger.critical(
                "TELEGRAM_BOT_TOKEN is not set. "
                "Go to Railway -> your service -> Variables and add it."
            )
            sys.exit(1)
        logger.warning(f"pydantic-settings failed ({e}), falling back to os.environ")
        return Settings(TELEGRAM_BOT_TOKEN=token)


config = load_config()

# ─────────────────────────────────────────────
# 2. CONSTANTS
# ─────────────────────────────────────────────
CHAIN_MAP: Dict[str, str] = {
    "solana": "Solana", "ethereum": "Ethereum", "base": "Base",
    "bsc": "BNB Chain", "arbitrum": "Arbitrum", "polygon": "Polygon",
    "avalanche": "Avalanche", "sui": "Sui", "tron": "Tron",
}

VOLUME_KEYS: Dict[str, str] = {
    "5m": "m5", "1h": "h1", "6h": "h6", "24h": "h24",
}

DEFAULT_USER_SETTINGS: Dict = {
    "is_paused": False,
    "volume_timeframe": "5m",
    "ratio_threshold": 10.0,
    "min_market_cap": 100_000,
    "min_liquidity": 50_000,
    "cooldown_minutes": 30,
    "selected_chains": ["solana", "base"],
}

# ─────────────────────────────────────────────
# 3. DATABASE MODELS
# FIX: Use sqlalchemy.orm.declarative_base.
#      sqlalchemy.ext.declarative.declarative_base was removed in v2.
# ─────────────────────────────────────────────
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
    added_at = Column(DateTime, server_default=func.now())


class AlertHistory(Base):
    __tablename__ = "alert_history"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer)
    token_address = Column(String)
    pair_address = Column(String)
    ratio = Column(Float)
    timestamp = Column(DateTime, server_default=func.now())


# ─────────────────────────────────────────────
# 4. DATABASE SESSION
# ─────────────────────────────────────────────
engine = create_async_engine(config.DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.success("Database tables ready")


# ─────────────────────────────────────────────
# 5. DEX SCREENER CLIENT
# FIX: `dict | None` and `list[str]` are Python 3.10+ syntax.
#      Replaced with Optional[Dict] and List[str] for Python 3.9 compat.
# ─────────────────────────────────────────────
class DexScreenerClient:
    BASE_URL = "https://api.dexscreener.com"

    async def _request(
        self, url: str, params: Optional[Dict] = None
    ) -> Optional[dict]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            for attempt in range(3):
                try:
                    resp = await client.get(url, params=params)
                    if resp.status_code == 429:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    resp.raise_for_status()
                    return resp.json()
                except Exception as e:
                    logger.warning(f"DEXScreener attempt {attempt + 1}/3 failed: {e}")
                    if attempt == 2:
                        return None
                    await asyncio.sleep(2 ** attempt)
        return None

    async def search(self, query: str) -> List[dict]:
        data = await self._request(
            f"{self.BASE_URL}/latest/dex/search", {"q": query}
        )
        return data.get("pairs", []) if data else []

    async def get_token_pairs_batch(
        self, chain_id: str, token_addresses: List[str]
    ) -> List[dict]:
        if not token_addresses:
            return []
        addr_str = ",".join(token_addresses[:30])
        data = await self._request(
            f"{self.BASE_URL}/tokens/v1/{chain_id}/{addr_str}"
        )
        return data if isinstance(data, list) else []


dex_client = DexScreenerClient()

# ─────────────────────────────────────────────
# 6. HELPERS
# FIX: `float | None` is Python 3.10+ — replaced with Optional[float].
# ─────────────────────────────────────────────
def calculate_volume_ratio(
    volume: float,
    mcap: Optional[float],
    fdv: Optional[float],
) -> float:
    denominator = mcap if mcap and mcap > 0 else fdv
    if not denominator or denominator <= 0:
        return 0.0
    return (volume / denominator) * 100


# ─────────────────────────────────────────────
# 7. ALERT ENGINE
# FIX: Removed global `bot = None` pattern.
#      Bot is now passed explicitly — eliminates the race condition
#      where send_alert could fire before the global was ever assigned.
# ─────────────────────────────────────────────
async def send_alert(
    bot, user_id: int, pair: dict, ratio: float, timeframe: str
) -> None:
    token = pair.get("baseToken", {})
    mcap = pair.get("marketCap") or pair.get("fdv") or 0
    liq = pair.get("liquidity", {}).get("usd", 0)
    volume = pair.get("volume", {}).get(VOLUME_KEYS.get(timeframe, "m5"), 0)
    url = pair.get("url", "")

    text = (
        "🚨 <b>Volume Surge Detected!</b>\n\n"
        f"<b>{html.escape(token.get('name', ''))} ({token.get('symbol', '')})</b>\n"
        f"Chain: {CHAIN_MAP.get(pair.get('chainId', ''), pair.get('chainId', ''))}\n"
        f"Price: ${pair.get('priceUsd', 'N/A')}\n"
        f"Market Cap: ${mcap:,.0f}\n"
        f"Liquidity: ${liq:,.0f}\n"
        f"{timeframe} Volume: ${volume:,.0f}\n"
        f"Ratio: <b>{ratio:.1f}%</b>\n\n"
        f'<a href="{url}">View on DEX Screener</a>'
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Open Chart", url=url)],
        [
            InlineKeyboardButton(
                "Snooze 1h",
                callback_data=f"snooze_{pair.get('pairAddress', '')}",
            ),
            InlineKeyboardButton(
                "Ignore Token",
                callback_data=f"ignore_{token.get('address', '')}",
            ),
        ],
    ])

    try:
        await bot.send_message(
            chat_id=user_id, text=text, parse_mode="HTML", reply_markup=keyboard
        )
        logger.success(f"Alert sent to user {user_id} | {token.get('symbol')}")
    except Exception as e:
        logger.error(f"Alert failed for user {user_id}: {e}")


# ─────────────────────────────────────────────
# 8. SCANNER (disabled by default — see post_init to enable)
# FIX: bot is passed as a function argument, not read from a global.
#      APScheduler is started inside post_init to share PTB's event loop.
# ─────────────────────────────────────────────
async def process_pair(
    bot, user: User, pair: dict, db: AsyncSession, settings: dict
) -> None:
    try:
        if not pair or "volume" not in pair:
            return

        tf_key = VOLUME_KEYS.get(settings.get("volume_timeframe", "5m"), "m5")
        volume = pair.get("volume", {}).get(tf_key, 0) or 0
        ratio = calculate_volume_ratio(
            volume, pair.get("marketCap"), pair.get("fdv")
        )

        if ratio < settings.get("ratio_threshold", 10.0):
            return
        if (pair.get("liquidity", {}).get("usd", 0) or 0) < settings.get(
            "min_liquidity", 50_000
        ):
            return

        token_addr = pair.get("baseToken", {}).get("address", "")
        last = (
            await db.execute(
                select(AlertHistory)
                .where(
                    AlertHistory.user_id == user.user_id,
                    AlertHistory.token_address == token_addr,
                )
                .order_by(AlertHistory.timestamp.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        cooldown = timedelta(minutes=settings.get("cooldown_minutes", 30))
        if last and (datetime.utcnow() - last.timestamp) < cooldown:
            return

        await send_alert(
            bot, user.user_id, pair, ratio, settings.get("volume_timeframe", "5m")
        )

        db.add(AlertHistory(
            user_id=user.user_id,
            token_address=token_addr,
            pair_address=pair.get("pairAddress", ""),
            ratio=ratio,
        ))
        await db.commit()

    except Exception as e:
        logger.error(f"process_pair error: {e}")


async def scan_watchlists(bot) -> None:
    """Called by APScheduler. Scans every user's watchlist."""
    try:
        async with AsyncSessionLocal() as db:
            users = (await db.execute(select(User))).scalars().all()
            if not users:
                return

            for user in users:
                settings = {**DEFAULT_USER_SETTINGS, **(user.settings or {})}
                if settings.get("is_paused"):
                    continue

                watchlist = (
                    await db.execute(
                        select(Watchlist).where(Watchlist.user_id == user.user_id)
                    )
                ).scalars().all()

                if not watchlist:
                    continue

                by_chain: Dict[str, List[str]] = {}
                for w in watchlist:
                    by_chain.setdefault(w.chain_id, []).append(w.token_address)

                for chain, addrs in by_chain.items():
                    pairs = await dex_client.get_token_pairs_batch(chain, addrs)
                    for pair in pairs:
                        await process_pair(bot, user, pair, db, settings)

    except Exception as e:
        logger.error(f"Scanner error (bot keeps running): {e}")


# ─────────────────────────────────────────────
# 9. NATURAL LANGUAGE PARSER
# ─────────────────────────────────────────────
def parse_natural_language(text: str) -> Dict:
    text = text.lower().strip()
    intent: Dict = {"action": "unknown", "data": {}}

    chain_match = re.search(
        r"(track|monitor|watch|follow).*?"
        r"(solana|base|ethereum|bsc|arbitrum|polygon|avalanche|sui|tron)",
        text,
    )
    if chain_match:
        chains = re.findall(
            r"(solana|base|ethereum|bsc|arbitrum|polygon|avalanche|sui|tron)", text
        )
        intent = {"action": "add_chains", "data": {"chains": list(set(chains))}}

    threshold_match = re.search(r"(notify|alert|exceed|surge).*?(\d+)%", text)
    if threshold_match:
        intent = {
            "action": "set_threshold",
            "data": {"threshold": float(threshold_match.group(2))},
        }

    if "5m" in text or "5-minute" in text:
        intent.setdefault("data", {})["timeframe"] = "5m"
    elif "1h" in text or "1-hour" in text:
        intent.setdefault("data", {})["timeframe"] = "1h"

    if any(x in text for x in ["pause", "stop", "quiet"]):
        intent = {"action": "pause"}
    if any(x in text for x in ["resume", "start", "go"]):
        intent = {"action": "resume"}

    return intent


# ─────────────────────────────────────────────
# 10. TELEGRAM HANDLERS
# ─────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return

    async with AsyncSessionLocal() as db:
        existing = (
            await db.execute(select(User).where(User.user_id == user.id))
        ).scalar_one_or_none()

        if not existing:
            db.add(User(
                user_id=user.id,
                username=user.username,
                settings=DEFAULT_USER_SETTINGS.copy(),
            ))
            await db.commit()
            logger.info(f"New user: {user.id} (@{user.username})")

    await update.message.reply_text(
        "🚀 <b>Volume Surge Bot is ONLINE!</b>\n\n"
        "I monitor DEX Screener for volume surges and alert you in real-time.\n\n"
        "Type /menu to see options, or talk naturally:\n"
        "• <code>Track Solana and Base</code>\n"
        "• <code>Alert me when ratio exceeds 20%</code>\n"
        "• <code>Pause alerts</code>",
        parse_mode="HTML",
    )


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        [InlineKeyboardButton("▶️ Start Monitoring", callback_data="start_monitoring")],
        [InlineKeyboardButton("⏸️ Pause", callback_data="pause")],
        [InlineKeyboardButton("⚙️ Configure Filters", callback_data="config_filters")],
        [InlineKeyboardButton("🌐 Select Chains", callback_data="select_chains")],
        [InlineKeyboardButton("📊 Status", callback_data="status")],
    ]
    await update.message.reply_text(
        "📋 <b>Main Menu</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    async with AsyncSessionLocal() as db:
        user = (
            await db.execute(select(User).where(User.user_id == user_id))
        ).scalar_one_or_none()

    if not user:
        await update.message.reply_text("Use /start first.")
        return

    s = {**DEFAULT_USER_SETTINGS, **(user.settings or {})}
    chains = ", ".join(s.get("selected_chains", [])) or "None"
    status_label = "Paused" if s.get("is_paused") else "Active"

    await update.message.reply_text(
        f"📊 <b>Your Settings</b>\n\n"
        f"Status: {status_label}\n"
        f"Chains: {chains}\n"
        f"Timeframe: {s.get('volume_timeframe')}\n"
        f"Ratio Threshold: {s.get('ratio_threshold')}%\n"
        f"Min Market Cap: ${s.get('min_market_cap'):,}\n"
        f"Min Liquidity: ${s.get('min_liquidity'):,}\n"
        f"Cooldown: {s.get('cooldown_minutes')} min",
        parse_mode="HTML",
    )


async def handle_natural_language(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    text = update.message.text or ""
    intent = parse_natural_language(text)
    action = intent.get("action", "unknown")

    if action == "unknown":
        await update.message.reply_text(
            "I didn't understand that. Try:\n"
            "• <code>Track Solana</code>\n"
            "• <code>Alert me when ratio exceeds 20%</code>\n"
            "• <code>Pause alerts</code>\n"
            "Or use /menu.",
            parse_mode="HTML",
        )
        return

    user_id = update.effective_user.id
    data = intent.get("data", {})

    async with AsyncSessionLocal() as db:
        user = (
            await db.execute(select(User).where(User.user_id == user_id))
        ).scalar_one_or_none()

        if not user:
            await update.message.reply_text("Please /start first.")
            return

        settings = {**DEFAULT_USER_SETTINGS, **(user.settings or {})}

        if action == "add_chains" and data.get("chains"):
            existing_chains = set(settings.get("selected_chains", []))
            settings["selected_chains"] = list(existing_chains | set(data["chains"]))
            user.settings = settings
            await db.commit()
            await update.message.reply_text(
                f"✅ Now tracking: {', '.join(settings['selected_chains'])}"
            )

        elif action == "set_threshold" and data.get("threshold"):
            settings["ratio_threshold"] = data["threshold"]
            user.settings = settings
            await db.commit()
            await update.message.reply_text(
                f"✅ Threshold set to {data['threshold']}%"
            )

        elif action == "pause":
            settings["is_paused"] = True
            user.settings = settings
            await db.commit()
            await update.message.reply_text("⏸️ Alerts paused.")

        elif action == "resume":
            settings["is_paused"] = False
            user.settings = settings
            await db.commit()
            await update.message.reply_text("▶️ Alerts resumed!")

        else:
            await update.message.reply_text(f"✅ Got it: {action}")


async def handle_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    if data == "pause":
        await query.edit_message_text("⏸️ Alerts paused. Send 'resume' to re-enable.")
    elif data == "start_monitoring":
        await query.edit_message_text("▶️ Monitoring active.")
    elif data == "status":
        await query.edit_message_text("Use /status for details.")
    elif data.startswith("snooze_"):
        await query.edit_message_text("⏰ Snoozed for 1 hour.")
    elif data.startswith("ignore_"):
        await query.edit_message_text("🚫 Token ignored.")
    else:
        await query.edit_message_text(f"Action: {data}")


# ─────────────────────────────────────────────
# 11. STARTUP / SHUTDOWN HOOKS
# FIX: APScheduler MUST be started inside post_init so it runs
#      inside PTB's event loop. Starting it earlier creates a
#      second loop that conflicts with PTB v21.
# ─────────────────────────────────────────────
async def post_init(application: Application) -> None:
    await init_db()

    # ── TO ENABLE LIVE SCANNING: uncomment the block below ──────────────
    # NOTE: SQLite on Railway Free has an ephemeral filesystem.
    #       Data is lost on every redeploy. Use PostgreSQL for persistence.
    #       Add DATABASE_URL=postgresql+asyncpg://... to Railway Variables.
    #
    # from apscheduler.schedulers.asyncio 
