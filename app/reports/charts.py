from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def _finish(fig, path: Path):
    fig.autofmt_xdate()
    fig.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(fig)


def save_price_chart(dataframe, path: Path):
    fig, ax = plt.subplots(figsize=(10, 4.2))
    ax.plot(dataframe["trade_date"], dataframe["close_price"], label="Close")
    if dataframe["vwap"].notna().any():
        ax.plot(dataframe["trade_date"], dataframe["vwap"], label="VWAP", alpha=0.8)
    ax.set_title("Price and VWAP")
    ax.set_xlabel("Date")
    ax.set_ylabel("Price")
    ax.grid(alpha=0.2)
    ax.legend()
    _finish(fig, path)


def save_volume_chart(dataframe, path: Path):
    fig, ax = plt.subplots(figsize=(10, 4.2))
    ax.bar(dataframe["trade_date"], dataframe["volume"].fillna(0))
    ax.set_title("Trading Volume")
    ax.set_xlabel("Date")
    ax.set_ylabel("Volume")
    _finish(fig, path)


def save_risk_chart(dataframe, path: Path):
    fig, ax = plt.subplots(figsize=(10, 4.2))
    ax.plot(dataframe["trade_date"], dataframe["risk_score"], label="Overall risk")
    ax.plot(dataframe["trade_date"], dataframe["anomaly_score"], label="Anomaly", alpha=0.65)
    if dataframe["case_score"].max() > 0:
        ax.plot(dataframe["trade_date"], dataframe["case_score"], label="Case similarity", alpha=0.65)
    ax.axhline(70, linestyle="--", label="High-risk threshold")
    ax.set_ylim(0, 100)
    ax.set_title("Surveillance Risk Scores")
    ax.set_xlabel("Date")
    ax.set_ylabel("Score")
    ax.legend()
    _finish(fig, path)


def save_market_activity_chart(dataframe, path: Path):
    fig, ax = plt.subplots(figsize=(10, 4.2))
    plotted = False
    if dataframe["delivery_percentage"].notna().any():
        ax.plot(dataframe["trade_date"], dataframe["delivery_percentage"], label="Delivery %")
        plotted = True
    if dataframe["trades_ratio_20d"].notna().any():
        ax.plot(dataframe["trade_date"], dataframe["trades_ratio_20d"], label="Trade-count ratio (20D)")
        plotted = True
    ax.set_title("Delivery and Trading-Intensity Indicators")
    ax.set_xlabel("Date")
    ax.grid(alpha=0.2)
    if plotted:
        ax.legend()
    else:
        ax.text(0.5, 0.5, "No delivery/trade-count data available", ha="center", va="center", transform=ax.transAxes)
    _finish(fig, path)
