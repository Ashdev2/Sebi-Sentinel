import numpy as np
import pandas as pd


FEATURE_COLUMNS = [
    "return_1d",
    "return_5d",
    "volume_ratio_20d",
    "volume_zscore_20d",
    "volatility_20d",
    "vwap_deviation_pct",
    "trades_ratio_20d",
    "delivery_ratio_20d",
]


def engineer_features(dataframe: pd.DataFrame) -> pd.DataFrame:
    df = dataframe.copy().sort_values("trade_date").reset_index(drop=True)

    for column in ["close_price", "vwap", "volume", "number_of_trades", "delivery_percentage"]:
        if column not in df.columns:
            df[column] = np.nan

    df["return_1d"] = df["close_price"].pct_change(fill_method=None)
    df["return_5d"] = df["close_price"].pct_change(5, fill_method=None)

    previous_volume = df["volume"].shift(1)
    volume_average = previous_volume.rolling(window=20, min_periods=5).mean()
    volume_std = previous_volume.rolling(window=20, min_periods=5).std().replace(0, np.nan)
    df["volume_ratio_20d"] = df["volume"] / volume_average
    df["volume_zscore_20d"] = (df["volume"] - volume_average) / volume_std

    df["volatility_20d"] = df["return_1d"].shift(1).rolling(20, min_periods=5).std()

    valid_vwap = df["vwap"].replace(0, np.nan)
    df["vwap_deviation_pct"] = ((df["close_price"] - valid_vwap) / valid_vwap) * 100

    previous_trades = df["number_of_trades"].shift(1)
    trade_average = previous_trades.rolling(20, min_periods=5).mean().replace(0, np.nan)
    df["trades_ratio_20d"] = df["number_of_trades"] / trade_average

    previous_delivery = df["delivery_percentage"].shift(1)
    delivery_average = previous_delivery.rolling(20, min_periods=5).mean().replace(0, np.nan)
    df["delivery_ratio_20d"] = df["delivery_percentage"] / delivery_average

    return df
