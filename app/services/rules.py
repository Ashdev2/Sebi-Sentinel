import pandas as pd


def _safe(value, default=0.0):
    if value is None or pd.isna(value):
        return default
    return float(value)


def calculate_rule_score(row):
    score = 0.0
    reasons = []

    daily_return = abs(_safe(row.get("return_1d")))
    volume_ratio = _safe(row.get("volume_ratio_20d"))
    volume_zscore = abs(_safe(row.get("volume_zscore_20d")))
    trade_ratio = _safe(row.get("trades_ratio_20d"))
    delivery_ratio = _safe(row.get("delivery_ratio_20d"), 1.0)
    vwap_deviation = abs(_safe(row.get("vwap_deviation_pct")))

    if daily_return >= 0.08:
        score += 20
        reasons.append(f"Large one-day price movement of {daily_return * 100:.2f}%.")
    if volume_ratio >= 3:
        score += 25
        reasons.append(f"Trading volume was {volume_ratio:.2f}x its prior 20-day baseline.")
    if volume_zscore >= 3:
        score += 15
        reasons.append(f"Volume z-score reached {volume_zscore:.2f}, indicating an extreme statistical deviation.")
    if trade_ratio >= 3:
        score += 15
        reasons.append(f"Trade count was {trade_ratio:.2f}x its prior 20-day baseline.")
    if delivery_ratio >= 2:
        score += 10
        reasons.append(f"Delivery percentage was {delivery_ratio:.2f}x its recent baseline.")
    if vwap_deviation >= 5:
        score += 15
        reasons.append(f"Closing price deviated from VWAP by {vwap_deviation:.2f}%.")

    if not reasons:
        reasons.append("No major rule-based anomaly threshold was triggered for this trading day.")

    return min(score, 100.0), reasons
