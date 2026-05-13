import re
from typing import Dict

def parse_natural_language(text: str) -> Dict:
    text = text.lower().strip()
    intent = {"action": "unknown", "data": {}}

    if re.search(r"(track|monitor|watch|follow).*?(solana|base|ethereum|bsc|arbitrum|polygon|avalanche|sui|tron)", text):
        chains = re.findall(r"(solana|base|ethereum|bsc|arbitrum|polygon|avalanche|sui|tron)", text)
        intent = {"action": "add_chains", "data": {"chains": list(set(chains))}}

    threshold_match = re.search(r"(notify|alert|exceed|surge).*?(\d+)%", text)
    if threshold_match:
        intent = {"action": "set_threshold", "data": {"threshold": float(threshold_match.group(2))}}

    if "5-minute" in text or "5m" in text:
        intent.setdefault("data", {})["timeframe"] = "5m"
    elif "1-hour" in text or "1h" in text:
        intent.setdefault("data", {})["timeframe"] = "1h"

    if any(x in text for x in ["pause", "stop", "quiet"]):
        intent = {"action": "pause"}
    if any(x in text for x in ["resume", "start", "go"]):
        intent = {"action": "resume"}

    return intent
