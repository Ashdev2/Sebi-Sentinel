import math

import pandas as pd

from app.services.explanations import explain_possible_mechanism


def clean_number(value, digits=None):
    if value is None or (isinstance(value, float) and (math.isnan(value) or math.isinf(value))) or pd.isna(value):
        return None
    value = float(value)
    return round(value, digits) if digits is not None else value


def build_analysis_payload(stock, dataframe, meta, cases):
    if dataframe.empty:
        raise ValueError("No data found in the selected period.")

    df = dataframe.sort_values("trade_date").copy()
    ranked = df.sort_values("risk_score", ascending=False)
    highest = ranked.iloc[0]
    first = df.iloc[0]
    last = df.iloc[-1]

    start_close = clean_number(first.get("close_price"), 2)
    end_close = clean_number(last.get("close_price"), 2)
    price_change_pct = None
    if start_close not in {None, 0} and end_close is not None:
        price_change_pct = round(((end_close / start_close) - 1) * 100, 2)

    reasons = list(highest.get("reasons") or [])
    mechanism = explain_possible_mechanism(df)

    top_anomalies = []
    for _, row in ranked.head(12).iterrows():
        top_anomalies.append({
            "date": str(row["trade_date"]),
            "close": clean_number(row.get("close_price"), 2),
            "daily_return_pct": None if pd.isna(row.get("return_1d")) else round(float(row.get("return_1d")) * 100, 2),
            "volume_ratio": clean_number(row.get("volume_ratio_20d"), 2),
            "trade_ratio": clean_number(row.get("trades_ratio_20d"), 2),
            "delivery_ratio": clean_number(row.get("delivery_ratio_20d"), 2),
            "vwap_deviation_pct": clean_number(row.get("vwap_deviation_pct"), 2),
            "anomaly_score": clean_number(row.get("anomaly_score"), 2),
            "case_score": clean_number(row.get("case_score"), 2),
            "rule_score": clean_number(row.get("rule_score"), 2),
            "risk_score": clean_number(row.get("risk_score"), 2),
            "pattern": str(row.get("suspected_pattern")),
        })

    chart_data = []
    for _, row in df.iterrows():
        chart_data.append({
            "date": str(row["trade_date"]),
            "open": clean_number(row.get("open_price"), 2),
            "high": clean_number(row.get("high_price"), 2),
            "low": clean_number(row.get("low_price"), 2),
            "close": clean_number(row.get("close_price"), 2),
            "vwap": clean_number(row.get("vwap"), 2),
            "volume": None if pd.isna(row.get("volume")) else int(row.get("volume")),
            "delivery_percentage": clean_number(row.get("delivery_percentage"), 2),
            "number_of_trades": None if pd.isna(row.get("number_of_trades")) else int(row.get("number_of_trades")),
            "volume_ratio": clean_number(row.get("volume_ratio_20d"), 2),
            "trade_ratio": clean_number(row.get("trades_ratio_20d"), 2),
            "delivery_ratio": clean_number(row.get("delivery_ratio_20d"), 2),
            "risk_score": clean_number(row.get("risk_score"), 2),
            "anomaly_score": clean_number(row.get("anomaly_score"), 2),
            "case_score": clean_number(row.get("case_score"), 2),
            "rule_score": clean_number(row.get("rule_score"), 2),
        })

    official_cases = [
        {
            "regulator": c.regulator,
            "case_title": c.case_title,
            "order_date": None if c.order_date is None else str(c.order_date),
            "period_start": None if c.period_start is None else str(c.period_start),
            "period_end": None if c.period_end is None else str(c.period_end),
            "violation": c.violation,
            "summary": c.summary,
            "source_url": c.source_url,
            "source_reference": c.source_reference,
        }
        for c in cases
    ]

    report_text = {
        "status": "OFFICIAL REGULATORY CASE MATCH" if cases else "MODEL-DETECTED SURVEILLANCE ALERT",
        "headline": f"{stock.company_name} ({stock.symbol}) surveillance analysis",
        "summary": (
            f"The selected period contains {len(df):,} trading sessions. The highest surveillance score was "
            f"{float(highest['risk_score']):.1f}/100 on {highest['trade_date']}, classified as {highest['risk_level']}. "
            f"The closest data-pattern label was {highest['suspected_pattern']}."
        ),
        "interpretation": (
            "A model alert indicates unusual market behaviour, not proof of fraud. "
            "Fraud or manipulation is described as confirmed only when supported by an official regulatory record."
        ),
    }

    return {
        "symbol": stock.symbol,
        "company_name": stock.company_name,
        "period": {"start": str(df.iloc[0]["trade_date"]), "end": str(df.iloc[-1]["trade_date"]), "rows": len(df)},
        "model": meta,
        "summary": {
            "start_close": start_close,
            "end_close": end_close,
            "price_change_percent": price_change_pct,
            "highest_price": clean_number(df["high_price"].max(), 2),
            "lowest_price": clean_number(df["low_price"].min(), 2),
            "average_volume": clean_number(df["volume"].mean(), 0),
            "max_volume": clean_number(df["volume"].max(), 0),
            "average_delivery_percentage": clean_number(df["delivery_percentage"].mean(), 2),
            "average_trades": clean_number(df["number_of_trades"].mean(), 0),
            "max_volume_ratio": clean_number(df["volume_ratio_20d"].max(), 2),
            "max_trade_ratio": clean_number(df["trades_ratio_20d"].max(), 2),
            "max_delivery_ratio": clean_number(df["delivery_ratio_20d"].max(), 2),
        },
        "highest_risk_date": str(highest["trade_date"]),
        "highest_risk_score": round(float(highest["risk_score"]), 2),
        "risk_level": str(highest["risk_level"]),
        "possible_pattern": str(highest["suspected_pattern"]),
        "reasons": reasons,
        "possible_sequence": mechanism,
        "official_case_count": len(cases),
        "official_cases": official_cases,
        "top_anomalies": top_anomalies,
        "chart_data": chart_data,
        "report_preview": report_text,
    }
