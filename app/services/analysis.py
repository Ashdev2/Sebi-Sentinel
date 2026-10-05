from datetime import date
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import DailyAnalysis, DailyPrice, Stock
from app.services.features import FEATURE_COLUMNS, engineer_features
from app.services.rules import calculate_rule_score
from app.services.stocks import resolve_stock

ANOMALY_MODEL_PATH = Path(settings.artifacts_dir) / "daily_isolation_forest.joblib"
CASE_MODEL_PATH = Path(settings.artifacts_dir) / "case_classifier.joblib"


def risk_level(score: float) -> str:
    if score >= 85:
        return "VERY HIGH"
    if score >= 70:
        return "HIGH"
    if score >= 50:
        return "MODERATE"
    if score >= 30:
        return "LOW"
    return "VERY LOW"


def classify_pattern(row) -> str:
    volume = 0.0 if pd.isna(row.get("volume_ratio_20d")) else float(row.get("volume_ratio_20d"))
    daily_return = 0.0 if pd.isna(row.get("return_1d")) else abs(float(row.get("return_1d")))
    delivery_ratio = 1.0 if pd.isna(row.get("delivery_ratio_20d")) else float(row.get("delivery_ratio_20d"))
    trade_ratio = 0.0 if pd.isna(row.get("trades_ratio_20d")) else float(row.get("trades_ratio_20d"))

    if volume >= 4 and daily_return >= 0.08:
        return "Price-Volume Expansion Pattern"
    if volume >= 5:
        return "Abnormal Volume Accumulation"
    if delivery_ratio >= 2.5:
        return "Abnormal Delivery Accumulation"
    if trade_ratio >= 4:
        return "Abnormal Trading-Intensity Pattern"
    if daily_return >= 0.10:
        return "Abnormal Price Movement"
    return "No Strong Manipulation Pattern"


def _safe_model_frame(dataframe, features):
    return dataframe[features].replace([np.inf, -np.inf], np.nan).infer_objects(copy=False).fillna(0)


def _persist_analysis(dataframe):
    if dataframe.empty:
        return
    db = SessionLocal()
    try:
        for _, row in dataframe.iterrows():
            daily_price_id = int(row["daily_price_id"])
            record = db.scalar(select(DailyAnalysis).where(DailyAnalysis.daily_price_id == daily_price_id))
            values = {
                "return_1d": None if pd.isna(row.get("return_1d")) else float(row.get("return_1d")),
                "return_5d": None if pd.isna(row.get("return_5d")) else float(row.get("return_5d")),
                "volume_ratio_20d": None if pd.isna(row.get("volume_ratio_20d")) else float(row.get("volume_ratio_20d")),
                "volume_zscore_20d": None if pd.isna(row.get("volume_zscore_20d")) else float(row.get("volume_zscore_20d")),
                "volatility_20d": None if pd.isna(row.get("volatility_20d")) else float(row.get("volatility_20d")),
                "vwap_deviation_pct": None if pd.isna(row.get("vwap_deviation_pct")) else float(row.get("vwap_deviation_pct")),
                "trades_ratio_20d": None if pd.isna(row.get("trades_ratio_20d")) else float(row.get("trades_ratio_20d")),
                "delivery_ratio_20d": None if pd.isna(row.get("delivery_ratio_20d")) else float(row.get("delivery_ratio_20d")),
                "anomaly_score": float(row.get("anomaly_score", 0) or 0),
                "rule_score": float(row.get("rule_score", 0) or 0),
                "case_score": float(row.get("case_score", 0) or 0),
                "risk_score": float(row.get("risk_score", 0) or 0),
                "risk_level": str(row.get("risk_level") or "VERY LOW"),
                "suspected_pattern": str(row.get("suspected_pattern") or "No Strong Manipulation Pattern"),
                "reasons": list(row.get("reasons") or []),
            }
            if record is None:
                record = DailyAnalysis(daily_price_id=daily_price_id, **values)
                db.add(record)
            else:
                for key, value in values.items():
                    setattr(record, key, value)
        db.commit()
    finally:
        db.close()


def analyze_stock(symbol: str, start_date: date, end_date: date, persist: bool = True):
    symbol = symbol.upper().strip()
    if end_date < start_date:
        raise ValueError("End date must be on or after start date.")

    db = SessionLocal()
    try:
        stock = resolve_stock(db, symbol)
        if stock is None:
            raise ValueError(f"{symbol} is not a unique stock in the local NSE catalog/database. Search and select the stock first, or import its historical CSV.")

        prices = db.scalars(
            select(DailyPrice)
            .where(DailyPrice.stock_id == stock.id, DailyPrice.trade_date <= end_date)
            .order_by(DailyPrice.trade_date)
        ).all()
    finally:
        db.close()

    if not prices:
        raise ValueError(f"No historical price data has been imported for {symbol}.")

    dataframe = pd.DataFrame([
        {
            "daily_price_id": p.id,
            "trade_date": p.trade_date,
            "open_price": p.open_price,
            "high_price": p.high_price,
            "low_price": p.low_price,
            "close_price": p.close_price,
            "vwap": p.vwap,
            "volume": p.volume,
            "turnover": p.turnover,
            "number_of_trades": p.number_of_trades,
            "delivery_percentage": p.delivery_percentage,
        }
        for p in prices
    ])

    dataframe = engineer_features(dataframe)

    if ANOMALY_MODEL_PATH.exists():
        artifact = joblib.load(ANOMALY_MODEL_PATH)
        model = artifact["model"]
        features = artifact.get("features", FEATURE_COLUMNS)
        decision = model.decision_function(_safe_model_frame(dataframe, features))
        dataframe["anomaly_score"] = 100 / (1 + np.exp(np.clip(12 * decision, -60, 60)))
    else:
        dataframe["anomaly_score"] = 0.0

    rule_scores, reasons = [], []
    for _, row in dataframe.iterrows():
        score, row_reasons = calculate_rule_score(row)
        rule_scores.append(score)
        reasons.append(row_reasons)
    dataframe["rule_score"] = rule_scores
    dataframe["reasons"] = reasons

    case_model_available = CASE_MODEL_PATH.exists()
    if case_model_available:
        artifact = joblib.load(CASE_MODEL_PATH)
        case_model = artifact["model"]
        case_features = artifact.get("features", FEATURE_COLUMNS)
        case_prob = case_model.predict_proba(_safe_model_frame(dataframe, case_features))[:, 1]
        dataframe["case_score"] = case_prob * 100
        dataframe["risk_score"] = (
            dataframe["anomaly_score"] * 0.45
            + dataframe["case_score"] * 0.40
            + dataframe["rule_score"] * 0.15
        )
        model_stack = ["Isolation Forest anomaly model", "Known-case HistGradientBoosting classifier", "Explainable rule engine"]
    else:
        dataframe["case_score"] = 0.0
        dataframe["risk_score"] = dataframe["anomaly_score"] * 0.70 + dataframe["rule_score"] * 0.30
        model_stack = ["Isolation Forest anomaly model", "Explainable rule engine"]

    dataframe["risk_level"] = dataframe["risk_score"].apply(risk_level)
    dataframe["suspected_pattern"] = dataframe.apply(classify_pattern, axis=1)

    selected = dataframe[(dataframe["trade_date"] >= start_date) & (dataframe["trade_date"] <= end_date)].copy()
    if persist and not selected.empty:
        _persist_analysis(selected)

    return stock, selected, {
        "case_classifier_available": case_model_available,
        "model_stack": model_stack,
        "weights": {"anomaly": 0.45, "known_case": 0.40, "rules": 0.15} if case_model_available else {"anomaly": 0.70, "rules": 0.30},
    }
