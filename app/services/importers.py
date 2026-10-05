import re
from pathlib import Path

import pandas as pd
from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import DailyPrice, RegulatoryCase, Stock


def clean_column_name(name):
    name = str(name).strip().lower()
    name = re.sub(r"[^a-z0-9]+", " ", name)
    return name.strip()


def to_float(value):
    if value is None or pd.isna(value):
        return None
    value = str(value).replace(",", "").replace("%", "").strip()
    if value.lower() in {"", "-", "--", "nan", "none"}:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def to_int(value):
    value = to_float(value)
    return None if value is None else int(value)


def first_value(row, *names):
    for name in names:
        if name in row.index:
            return row[name]
    return None


def parse_date(value):
    parsed = pd.to_datetime(value, dayfirst=False, errors="coerce")
    if pd.isna(parsed):
        parsed = pd.to_datetime(value, dayfirst=True, errors="coerce")
    return None if pd.isna(parsed) else parsed.date()


def import_stock_csv(csv_path, company_name=None, forced_symbol=None):
    csv_path = Path(csv_path)
    df = pd.read_csv(csv_path)
    if df.empty:
        raise ValueError("The uploaded CSV is empty.")

    df.columns = [clean_column_name(c) for c in df.columns]
    symbol_column = next((c for c in ["symbol", "security", "security symbol", "ticker"] if c in df.columns), None)

    if symbol_column is None and not forced_symbol:
        raise ValueError("Could not find a symbol column. Enter the NSE symbol when uploading this file.")

    if symbol_column:
        df["_symbol"] = df[symbol_column].astype(str).str.strip().str.upper()
    else:
        df["_symbol"] = forced_symbol.upper().strip()

    imported = 0
    symbols_imported = []
    db = SessionLocal()
    try:
        for symbol in sorted(df["_symbol"].dropna().unique()):
            if not symbol or symbol == "NAN":
                continue

            stock = db.scalar(select(Stock).where(Stock.symbol == symbol))
            if stock is None:
                stock = Stock(symbol=symbol, company_name=company_name or symbol)
                db.add(stock)
                db.flush()
            elif company_name:
                stock.company_name = company_name

            stock_rows = df[df["_symbol"] == symbol]
            for _, row in stock_rows.iterrows():
                date_value = first_value(row, "date", "timestamp", "trade date", "trading date", "traded date")
                trade_date = pd.to_datetime(date_value, dayfirst=True, errors="coerce")
                if pd.isna(trade_date):
                    trade_date = pd.to_datetime(date_value, dayfirst=False, errors="coerce")
                if pd.isna(trade_date):
                    continue

                series_value = first_value(row, "series")
                series = "EQ" if series_value is None or pd.isna(series_value) else str(series_value).strip()

                existing = db.scalar(
                    select(DailyPrice).where(
                        DailyPrice.stock_id == stock.id,
                        DailyPrice.trade_date == trade_date.date(),
                        DailyPrice.series == series,
                    )
                )

                values = {
                    "previous_close": to_float(first_value(row, "prev close", "previous close", "prevclose")),
                    "open_price": to_float(first_value(row, "open", "open price", "openprice")),
                    "high_price": to_float(first_value(row, "high", "high price", "highprice")),
                    "low_price": to_float(first_value(row, "low", "low price", "lowprice")),
                    "last_price": to_float(first_value(row, "last", "last price", "last traded price", "ltp")),
                    "close_price": to_float(first_value(row, "close", "close price", "closeprice", "closing price")),
                    "vwap": to_float(first_value(row, "vwap", "average price")),
                    "volume": to_int(first_value(row, "total traded quantity", "volume", "ttl trd qnty", "tottrdqty", "total traded qty")),
                    "turnover": to_float(first_value(row, "turnover", "turnover lacs", "turnover lakhs", "turnover in lacs")),
                    "number_of_trades": to_int(first_value(row, "no of trades", "number of trades", "total trades", "trades", "no of trade")),
                    "delivery_quantity": to_int(first_value(row, "deliverable qty", "deliverable quantity", "delivery quantity", "deliverable volume")),
                    "delivery_percentage": to_float(first_value(row, "dly qt to traded qty", "delivery percentage", "deliverable percent", "delivery percent", "delivery pct")),
                    "source_file": str(csv_path),
                }

                if values["close_price"] is None:
                    continue

                if existing:
                    for key, value in values.items():
                        setattr(existing, key, value)
                else:
                    db.add(DailyPrice(stock_id=stock.id, trade_date=trade_date.date(), series=series, **values))
                imported += 1

            symbols_imported.append(symbol)

        db.commit()

        ranges = {}
        for symbol in symbols_imported:
            stock = db.scalar(select(Stock).where(Stock.symbol == symbol))
            row_count, min_date, max_date = db.execute(
                select(func.count(DailyPrice.id), func.min(DailyPrice.trade_date), func.max(DailyPrice.trade_date))
                .where(DailyPrice.stock_id == stock.id)
            ).one()
            ranges[symbol] = {"rows": int(row_count or 0), "first_date": min_date, "last_date": max_date}

        return {"imported_or_updated_rows": imported, "symbols": symbols_imported, "ranges": ranges}
    finally:
        db.close()


def import_stock_master_csv(csv_path):
    """Import an NSE security master/list so the dashboard can discover stocks before history is loaded."""
    csv_path = Path(csv_path)
    df = pd.read_csv(csv_path)
    if df.empty:
        raise ValueError("The stock-master CSV is empty.")
    df.columns = [clean_column_name(c) for c in df.columns]

    symbol_col = next((c for c in ["symbol", "security symbol", "ticker"] if c in df.columns), None)
    company_col = next((c for c in ["name of company", "company name", "company", "security name", "name"] if c in df.columns), None)
    isin_col = next((c for c in ["isin number", "isin", "isin no"] if c in df.columns), None)
    series_col = next((c for c in ["series"] if c in df.columns), None)

    if not symbol_col:
        raise ValueError("Could not identify a Symbol column in the stock-master CSV.")

    upserted = 0
    db = SessionLocal()
    try:
        for _, row in df.iterrows():
            symbol = str(row.get(symbol_col, "")).strip().upper()
            if not symbol or symbol == "NAN":
                continue
            if series_col:
                series = str(row.get(series_col, "")).strip().upper()
                if series and series not in {"EQ", "BE", "BZ", "SM", "ST", "NAN"}:
                    # Keep the catalog focused on equity-like listings; do not reject if series missing.
                    pass
            company = symbol
            if company_col and not pd.isna(row.get(company_col)):
                company = str(row.get(company_col)).strip()
            isin = None
            if isin_col and not pd.isna(row.get(isin_col)):
                isin = str(row.get(isin_col)).strip()

            stock = db.scalar(select(Stock).where(Stock.symbol == symbol))
            if stock is None:
                stock = Stock(symbol=symbol, company_name=company or symbol, isin=isin)
                db.add(stock)
            else:
                if company:
                    stock.company_name = company
                if isin:
                    stock.isin = isin
            upserted += 1
        db.commit()
        return {"catalog_records_upserted": upserted}
    finally:
        db.close()


def import_cases_csv(csv_path):
    csv_path = Path(csv_path)
    df = pd.read_csv(csv_path)
    if df.empty:
        raise ValueError("The regulatory-case CSV is empty.")
    df.columns = [clean_column_name(c) for c in df.columns]
    required = {"symbol"}
    if not required.issubset(df.columns):
        raise ValueError("Regulatory case CSV must contain a 'symbol' column.")

    count = 0
    db = SessionLocal()
    try:
        for _, row in df.iterrows():
            symbol = str(row.get("symbol", "")).strip().upper()
            if not symbol or symbol == "NAN":
                continue
            stock = db.scalar(select(Stock).where(Stock.symbol == symbol))
            company = None if pd.isna(row.get("company name")) else str(row.get("company name"))
            if stock is None:
                stock = Stock(symbol=symbol, company_name=company or symbol)
                db.add(stock)
                db.flush()

            source_ref = None if pd.isna(row.get("source reference")) else str(row.get("source reference"))
            source_url = None if pd.isna(row.get("source url")) else str(row.get("source url"))
            title = "Official regulatory case" if pd.isna(row.get("case title")) else str(row.get("case title"))

            # Avoid duplicate re-imports when an identifying source reference/url + title matches.
            duplicate_query = select(RegulatoryCase).where(
                RegulatoryCase.stock_id == stock.id,
                RegulatoryCase.case_title == title,
            )
            if source_ref:
                duplicate_query = duplicate_query.where(RegulatoryCase.source_reference == source_ref)
            elif source_url:
                duplicate_query = duplicate_query.where(RegulatoryCase.source_url == source_url)
            duplicate = db.scalar(duplicate_query)
            if duplicate:
                continue

            case = RegulatoryCase(
                stock_id=stock.id,
                regulator="SEBI" if pd.isna(row.get("regulator")) else str(row.get("regulator")),
                case_title=title,
                order_date=parse_date(row.get("order date")),
                period_start=parse_date(row.get("period start")),
                period_end=parse_date(row.get("period end")),
                violation=None if pd.isna(row.get("violation")) else str(row.get("violation")),
                summary=None if pd.isna(row.get("summary")) else str(row.get("summary")),
                source_url=source_url,
                source_reference=source_ref,
                confirmed=True,
            )
            db.add(case)
            count += 1
        db.commit()
        return {"regulatory_cases_imported": count}
    finally:
        db.close()
