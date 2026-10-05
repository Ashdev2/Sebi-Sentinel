from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, IsolationForest
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.utils.class_weight import compute_sample_weight
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import DailyPrice, RegulatoryCase, Stock
from app.services.features import FEATURE_COLUMNS, engineer_features


def _stock_frame(prices):
    return pd.DataFrame([
        {
            "trade_date": p.trade_date,
            "close_price": p.close_price,
            "vwap": p.vwap,
            "volume": p.volume,
            "number_of_trades": p.number_of_trades,
            "delivery_percentage": p.delivery_percentage,
        }
        for p in prices
    ])


def train_models():
    db = SessionLocal()
    feature_frames = []
    labelled_frames = []
    stock_count = 0
    try:
        stocks = db.scalars(select(Stock)).all()
        for stock in stocks:
            prices = db.scalars(
                select(DailyPrice).where(DailyPrice.stock_id == stock.id).order_by(DailyPrice.trade_date)
            ).all()
            if len(prices) < 30:
                continue
            stock_count += 1
            df = engineer_features(_stock_frame(prices))
            feature_frames.append(df[FEATURE_COLUMNS])

            cases = db.scalars(
                select(RegulatoryCase).where(RegulatoryCase.stock_id == stock.id, RegulatoryCase.confirmed.is_(True))
            ).all()
            if cases:
                labelled = df.copy()
                labelled["label"] = 0
                for case in cases:
                    if case.period_start and case.period_end:
                        mask = (labelled["trade_date"] >= case.period_start) & (labelled["trade_date"] <= case.period_end)
                    elif case.period_start:
                        mask = labelled["trade_date"] >= case.period_start
                    elif case.period_end:
                        mask = labelled["trade_date"] <= case.period_end
                    else:
                        continue
                    labelled.loc[mask, "label"] = 1
                labelled_frames.append(labelled[FEATURE_COLUMNS + ["label"]])
    finally:
        db.close()

    if not feature_frames:
        raise RuntimeError("No training data found. Import at least one stock with 30+ daily rows first.")

    training = pd.concat(feature_frames, ignore_index=True)
    training = training.replace([np.inf, -np.inf], np.nan).infer_objects(copy=False).fillna(0)

    anomaly_model = Pipeline([
        ("scaler", StandardScaler()),
        ("isolation_forest", IsolationForest(n_estimators=350, contamination=0.03, random_state=42, n_jobs=-1)),
    ])
    anomaly_model.fit(training[FEATURE_COLUMNS])

    artifact_dir = Path(settings.artifacts_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    anomaly_path = artifact_dir / "daily_isolation_forest.joblib"
    joblib.dump({"model": anomaly_model, "features": FEATURE_COLUMNS}, anomaly_path)

    case_model_created = False
    case_rows = 0
    positive_rows = 0
    negative_rows = 0
    case_path = artifact_dir / "case_classifier.joblib"

    if labelled_frames:
        labelled = pd.concat(labelled_frames, ignore_index=True)
        labelled = labelled.replace([np.inf, -np.inf], np.nan).infer_objects(copy=False).fillna(0)
        case_rows = len(labelled)
        positive_rows = int(labelled["label"].sum())
        negative_rows = int((labelled["label"] == 0).sum())

        if positive_rows >= 10 and negative_rows >= 20:
            X = labelled[FEATURE_COLUMNS]
            y = labelled["label"].astype(int)
            sample_weight = compute_sample_weight(class_weight="balanced", y=y)
            classifier = HistGradientBoostingClassifier(
                learning_rate=0.06,
                max_iter=250,
                max_leaf_nodes=15,
                l2_regularization=0.5,
                random_state=42,
            )
            classifier.fit(X, y, sample_weight=sample_weight)
            joblib.dump({"model": classifier, "features": FEATURE_COLUMNS}, case_path)
            case_model_created = True

    return {
        "stocks_used": stock_count,
        "anomaly_training_rows": len(training),
        "anomaly_model": str(anomaly_path),
        "case_classifier_created": case_model_created,
        "case_training_rows": case_rows,
        "case_positive_rows": positive_rows,
        "case_negative_rows": negative_rows,
        "case_classifier": str(case_path) if case_model_created else None,
    }
