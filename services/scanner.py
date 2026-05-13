import asyncio
from datetime import datetime, timedelta
from loguru import logger
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from database.session import AsyncSessionLocal
from database.models import User, Watchlist, AlertHistory
from services.dexscreener_client import DexScreenerClient
from services.alert_engine import send_alert
from utils.constants import VOLUME_KEYS, CHAIN_MAP
from utils.helpers import calculate_volume_ratio
from config import config

client = DexScreenerClient()
scheduler = AsyncIOScheduler()

async def scan_watchlists():
    try:
        async with AsyncSessionLocal() as db:
            users = (await db.execute(select(User))).scalars().all()
            
            if not users:
                logger.debug("No users yet - scanner running but nothing to monitor")
                return

            for user in users:
                settings = user.settings or {}
                if settings.get("is_paused", False):
                    continue

                watchlist = (await db.execute(
                    select(Watchlist).where(Watchlist.user_id == user.user_id)
                )).scalars().all()

                if not watchlist:
                    continue

                by_chain = {}
                for w in watchlist:
                    by_chain.setdefault(w.chain_id, []).append(w.token_address)

                for chain, addrs in by_chain.items():
                    pairs_data = await client.get_token_pairs_batch(chain, addrs)
                    for pair in pairs_data:
                        await process_pair(user, pair, db, settings)
    except Exception as e:
        logger.error(f"Scanner error (bot will continue running): {e}")

async def process_pair(user, pair: dict, db, settings: dict):
    try:
        if not pair or "volume" not in pair:
            return

        tf_key = VOLUME_KEYS.get(settings.get("volume_timeframe", "5m"), "m5")
        volume = pair.get("volume", {}).get(tf_key, 0)
        mcap = pair.get("marketCap")
        fdv = pair.get("fdv")
        ratio = calculate_volume_ratio(volume, mcap, fdv)

        if ratio < settings.get("ratio_threshold", 10.0):
            return

        if pair.get("liquidity", {}).get("usd", 0) < settings.get("min_liquidity", 50_000):
            return

        # cooldown check
        last = (await db.execute(
            select(AlertHistory)
            .where(AlertHistory.user_id == user.user_id,
                   AlertHistory.token_address == pair.get("baseToken", {}).get("address"))
            .order_by(AlertHistory.timestamp.desc())
            .limit(1)
        )).scalar_one_or_none()

        if last and datetime.utcnow() - last.timestamp < timedelta(minutes=settings.get("cooldown_minutes", 30)):
            return

        await send_alert(user.user_id, pair, ratio, settings.get("volume_timeframe", "5m"))
        
        db.add(AlertHistory(
            user_id=user.user_id,
            token_address=pair.get("baseToken", {}).get("address", ""),
            pair_address=pair.get("pairAddress", ""),
            ratio=ratio
        ))
        await db.commit()
        logger.success(f"🚨 Alert sent to {user.user_id}")
    except Exception as e:
        logger.error(f"Error processing pair: {e}")

def start_scanner():
    scheduler.add_job(scan_watchlists, "interval", seconds=config.SCAN_INTERVAL_SECONDS, id="scanner")
    scheduler.start()
    logger.success("📡 Live scanner started (crash-proof)")
