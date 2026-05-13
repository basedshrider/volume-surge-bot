import httpx
import asyncio
from loguru import logger
from config import config

class DexScreenerClient:
    BASE_URL = "https://api.dexscreener.com"

    async def _request(self, url: str, params: dict | None = None):
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
                    logger.warning(f"DEXScreener request failed {attempt+1}/3: {e}")
                    if attempt == 2:
                        raise
                    await asyncio.sleep(2 ** attempt)
        return None

    async def search(self, query: str):
        data = await self._request(f"{self.BASE_URL}/latest/dex/search", {"q": query})
        return data.get("pairs", []) if data else []

    async def get_token_pairs_batch(self, chain_id: str, token_addresses: list[str]):
        if not token_addresses:
            return []
        addr_str = ",".join(token_addresses[:30])
        data = await self._request(f"{self.BASE_URL}/tokens/v1/{chain_id}/{addr_str}")
        return data if isinstance(data, list) else []
