def calculate_volume_ratio(volume: float, mcap: float | None, fdv: float | None) -> float:
    denominator = mcap if mcap and mcap > 0 else fdv
    if not denominator or denominator <= 0:
        return 0.0
    return (volume / denominator) * 100
