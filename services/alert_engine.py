from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from loguru import logger
import html
from utils.constants import CHAIN_MAP, VOLUME_KEYS

# Global bot instance (set from bot.py)
bot = None

async def send_alert(user_id: int, pair: dict, ratio: float, timeframe: str):
    if bot is None:
        logger.error("Bot instance not set for alerts")
        return

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
        await bot.send_message(
            chat_id=user_id,
            text=text,
            parse_mode="HTML",
            reply_markup=keyboard
        )
        logger.success(f"✅ Alert sent to {user_id} for {token.get('symbol')}")
    except Exception as e:
        logger.error(f"❌ Failed to send alert to {user_id}: {e}")
