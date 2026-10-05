import pandas as pd


def explain_possible_mechanism(dataframe: pd.DataFrame) -> list[str]:
    """Create cautious, data-grounded phases. These are hypotheses, not findings of intent."""
    if dataframe.empty:
        return []

    df = dataframe.sort_values("trade_date").copy()
    peak = df.sort_values("risk_score", ascending=False).iloc[0]
    peak_date = peak["trade_date"]
    peak_position = df.index.get_loc(peak.name)

    phases = []
    before = df.iloc[max(0, peak_position - 10):peak_position]
    after = df.iloc[peak_position + 1:peak_position + 11]

    if not before.empty:
        before_vol = before["volume_ratio_20d"].dropna()
        before_ret = before["return_1d"].dropna()
        if (before_vol >= 1.5).sum() >= 2:
            phases.append(
                "Possible build-up phase: trading volume increased above its recent baseline on multiple sessions before the peak alert. "
                "This can be consistent with increased accumulation or market interest, but the data alone cannot identify who traded or why."
            )
        elif (before_ret.abs() >= 0.04).sum() >= 2:
            phases.append(
                "Possible pre-event price expansion: several material price moves occurred before the strongest alert, indicating that unusual behaviour developed over multiple sessions rather than a single isolated day."
            )

    volume_ratio = 0.0 if pd.isna(peak.get("volume_ratio_20d")) else float(peak.get("volume_ratio_20d"))
    one_day_return = 0.0 if pd.isna(peak.get("return_1d")) else float(peak.get("return_1d"))
    trade_ratio = 0.0 if pd.isna(peak.get("trades_ratio_20d")) else float(peak.get("trades_ratio_20d"))
    delivery_ratio = 1.0 if pd.isna(peak.get("delivery_ratio_20d")) else float(peak.get("delivery_ratio_20d"))

    signals = []
    if abs(one_day_return) >= 0.05:
        signals.append(f"a {one_day_return * 100:+.2f}% one-day price move")
    if volume_ratio >= 2:
        signals.append(f"volume at {volume_ratio:.2f}x baseline")
    if trade_ratio >= 2:
        signals.append(f"trade count at {trade_ratio:.2f}x baseline")
    if delivery_ratio >= 1.5:
        signals.append(f"delivery activity at {delivery_ratio:.2f}x baseline")

    if signals:
        phases.append(
            f"Peak alert on {peak_date}: " + ", ".join(signals) + ". The concurrence of multiple abnormal indicators is why this date received the highest surveillance score."
        )

    if not after.empty and pd.notna(peak.get("close_price")):
        peak_close = float(peak["close_price"])
        min_after = after["close_price"].min()
        max_after = after["close_price"].max()
        if pd.notna(min_after) and peak_close and (float(min_after) / peak_close - 1) <= -0.10:
            decline = (float(min_after) / peak_close - 1) * 100
            phases.append(
                f"Post-peak reversal: within the following sessions the closing price fell as much as {decline:.2f}% from the peak-alert close. "
                "A sharp reversal after abnormal price-volume expansion can resemble historical pump-and-dump-like market patterns, but this is not proof that such conduct occurred."
            )
        elif pd.notna(max_after) and peak_close and (float(max_after) / peak_close - 1) >= 0.10:
            rise = (float(max_after) / peak_close - 1) * 100
            phases.append(
                f"Post-peak continuation: the price continued as much as {rise:.2f}% above the peak-alert close, suggesting sustained market interest rather than an immediate reversal."
            )

    if not phases:
        phases.append(
            "The selected period contains statistical anomalies, but the available price/volume features do not support a clear multi-stage mechanism. Additional evidence such as order-level data, beneficial ownership, announcements, and official investigative findings would be required for a stronger explanation."
        )

    return phases
