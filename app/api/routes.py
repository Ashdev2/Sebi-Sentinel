from __future__ import annotations

import asyncio
from datetime import date, timedelta
from pathlib import Path
import shutil
import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select

from app.database import SessionLocal
from app.models import DailyPrice, GeneratedReport, Stock
from app.reports.generator import create_report
from app.services.analysis import analyze_stock
from app.services.cases import find_cases
from app.services.importers import import_cases_csv, import_stock_csv, import_stock_master_csv
from app.services.nse_mcp import (
    NSEProviderError,
    ensure_nse_stock_history,
    fetch_live_quote,
    search_nse_stocks,
)
from app.services.summary import build_analysis_payload
from app.services.stocks import resolve_stock
from app.services.training import train_models

router = APIRouter(prefix="/api")
UPLOAD_DIR = Path("data/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _save_upload(upload: UploadFile) -> Path:
    suffix = Path(upload.filename or "upload.csv").suffix or ".csv"
    path = UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
    with path.open("wb") as target:
        shutil.copyfileobj(upload.file, target)
    return path


def _local_stock_rows(q: str, limit: int = 30) -> list[dict]:
    db = SessionLocal()
    try:
        statement = select(Stock)
        if q.strip():
            statement = statement.where(or_(Stock.symbol.ilike(f"%{q}%"), Stock.company_name.ilike(f"%{q}%")))
        stocks = db.scalars(statement.order_by(Stock.symbol).limit(limit)).all()
        results = []
        for stock in stocks:
            count, first_date, last_date = db.execute(
                select(func.count(DailyPrice.id), func.min(DailyPrice.trade_date), func.max(DailyPrice.trade_date))
                .where(DailyPrice.stock_id == stock.id)
            ).one()
            results.append({
                "symbol": stock.symbol,
                "company_name": stock.company_name,
                "isin": stock.isin,
                "historical_rows": int(count or 0),
                "first_date": first_date,
                "last_date": last_date,
                "has_history": bool(count),
                "source": "local",
            })
        return results
    finally:
        db.close()


@router.get("/health")
def health():
    return {
        "status": "ok",
        "project": "SEBI Sentinel",
        "version": "3.0-automated-nse",
        "market_data": "Official NSE MCP + PostgreSQL cache",
    }


@router.get("/stocks/search")
async def search_stocks(q: str = ""):
    q = q.strip()
    local = _local_stock_rows(q, 30)
    if len(q) < 1:
        return {"results": local, "nse_online": None, "message": "Type a symbol or company name."}

    nse_online = True
    message = "Live NSE symbol search"
    try:
        remote = await search_nse_stocks(q, limit=15)
    except Exception as exc:
        remote = []
        nse_online = False
        message = f"NSE MCP unavailable; showing local database results only. {exc}"

    local_by_symbol = {item["symbol"]: item for item in local}
    merged: list[dict] = []
    seen: set[str] = set()
    for item in remote:
        symbol = item["symbol"]
        local_item = local_by_symbol.get(symbol)
        if local_item:
            merged.append({**item, **local_item, "source": "NSE + local cache"})
        else:
            merged.append({
                **item,
                "historical_rows": 0,
                "first_date": None,
                "last_date": None,
                "has_history": False,
                "source": "NSE live search",
            })
        seen.add(symbol)
    for item in local:
        if item["symbol"] not in seen:
            merged.append(item)

    return {"results": merged[:30], "nse_online": nse_online, "message": message}


@router.get("/stocks/{symbol}/range")
def stock_range(symbol: str):
    db = SessionLocal()
    try:
        stock = resolve_stock(db, symbol)
        if stock is None:
            return {
                "symbol": symbol.upper(),
                "company_name": symbol.upper(),
                "rows": 0,
                "first_date": None,
                "last_date": None,
                "has_history": False,
            }
        count, first_date, last_date = db.execute(
            select(func.count(DailyPrice.id), func.min(DailyPrice.trade_date), func.max(DailyPrice.trade_date))
            .where(DailyPrice.stock_id == stock.id)
        ).one()
        return {
            "symbol": stock.symbol,
            "company_name": stock.company_name,
            "rows": int(count or 0),
            "first_date": first_date,
            "last_date": last_date,
            "has_history": bool(count),
        }
    finally:
        db.close()


@router.post("/stocks/{symbol}/sync")
async def sync_stock(symbol: str, start: date | None = None, end: date | None = None):
    end = end or date.today()
    start = start or (end - timedelta(days=366 * 5))
    try:
        result = await ensure_nse_stock_history(symbol, start, end)
        if result.get("new_rows", 0) >= 20:
            result["training"] = await asyncio.to_thread(train_models)
        return {"status": "success", **result}
    except NSEProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


# Optional fallback/manual tools are deliberately kept out of the main dashboard.
@router.post("/import/stock-csv")
def upload_stock_csv(file: UploadFile = File(...), symbol: str = Form(""), company_name: str = Form("")):
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Please upload a CSV file.")
    path = _save_upload(file)
    try:
        result = import_stock_csv(path, company_name=company_name.strip() or None, forced_symbol=symbol.strip() or None)
        return {"status": "success", **result}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/import/stock-master")
def upload_stock_master(file: UploadFile = File(...)):
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Please upload a CSV file.")
    path = _save_upload(file)
    try:
        return {"status": "success", **import_stock_master_csv(path)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/import/cases-csv")
def upload_cases(file: UploadFile = File(...)):
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Please upload a CSV file.")
    path = _save_upload(file)
    try:
        return {"status": "success", **import_cases_csv(path)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/model/train")
def train_model_endpoint():
    try:
        return {"status": "success", **train_models()}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/history/{symbol}")
def history(symbol: str, start: date | None = None, end: date | None = None):
    db = SessionLocal()
    try:
        stock = db.scalar(select(Stock).where(Stock.symbol == symbol.upper()))
        if stock is None:
            raise HTTPException(status_code=404, detail="Stock not cached yet. Analyze it once to retrieve NSE history automatically.")
        query = select(DailyPrice).where(DailyPrice.stock_id == stock.id)
        if start:
            query = query.where(DailyPrice.trade_date >= start)
        if end:
            query = query.where(DailyPrice.trade_date <= end)
        rows = db.scalars(query.order_by(DailyPrice.trade_date)).all()
        return {
            "symbol": stock.symbol,
            "company_name": stock.company_name,
            "data": [
                {
                    "date": r.trade_date,
                    "open": r.open_price,
                    "high": r.high_price,
                    "low": r.low_price,
                    "close": r.close_price,
                    "vwap": r.vwap,
                    "volume": r.volume,
                    "trades": r.number_of_trades,
                    "delivery_percentage": r.delivery_percentage,
                }
                for r in rows
            ],
        }
    finally:
        db.close()


@router.get("/cases/{symbol}")
def cases_for_stock(symbol: str, start: date, end: date):
    cases = find_cases(symbol, start, end)
    return {
        "symbol": symbol.upper(),
        "count": len(cases),
        "cases": [
            {
                "regulator": c.regulator,
                "case_title": c.case_title,
                "order_date": c.order_date,
                "period_start": c.period_start,
                "period_end": c.period_end,
                "violation": c.violation,
                "summary": c.summary,
                "source_url": c.source_url,
                "source_reference": c.source_reference,
            }
            for c in cases
        ],
    }


@router.get("/analyze/{symbol_or_name}")
async def analyze(symbol_or_name: str, start: date, end: date):
    if end < start:
        raise HTTPException(status_code=400, detail="End date must be on or after start date.")

    try:
        sync = await ensure_nse_stock_history(symbol_or_name, start, end)
    except NSEProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    symbol = sync["symbol"]
    training = None
    if sync.get("new_rows", 0) >= 20:
        try:
            training = await asyncio.to_thread(train_models)
        except Exception as exc:
            training = {"warning": str(exc)}

    try:
        stock, df, meta = await asyncio.to_thread(analyze_stock, symbol, start, end)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error))
    if df.empty:
        raise HTTPException(status_code=404, detail="No data found in the selected period.")

    cases = await asyncio.to_thread(find_cases, stock.symbol, start, end)
    payload = build_analysis_payload(stock, df, meta, cases)
    payload["data_source"] = {
        "provider": "National Stock Exchange of India",
        "transport": "Official NSE MCP",
        "sync": sync,
        "auto_retrained": training,
        "history_limit": "NSE Bhavcopy MCP currently advertises the latest 5 years of historical data.",
    }
    payload["live_quote"] = await fetch_live_quote(stock.symbol)
    return payload


@router.get("/reports/{symbol}")
async def report(symbol: str, start: date, end: date):
    try:
        await ensure_nse_stock_history(symbol, start, end)
        pdf = await asyncio.to_thread(create_report, symbol, start, end)
    except (ValueError, NSEProviderError) as error:
        raise HTTPException(status_code=404, detail=str(error))
    return FileResponse(path=pdf, filename=pdf.name, media_type="application/pdf")


@router.get("/reports/{symbol}/view")
async def report_inline(symbol: str, start: date, end: date):
    try:
        await ensure_nse_stock_history(symbol, start, end)
        pdf = await asyncio.to_thread(create_report, symbol, start, end)
    except (ValueError, NSEProviderError) as error:
        raise HTTPException(status_code=404, detail=str(error))
    return FileResponse(
        path=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{pdf.name}"'},
    )


@router.get("/reports/recent/{symbol}")
def recent_reports(symbol: str, limit: int = 10):
    db = SessionLocal()
    try:
        stock = db.scalar(select(Stock).where(Stock.symbol == symbol.upper()))
        if stock is None:
            return []
        reports = db.scalars(
            select(GeneratedReport)
            .where(GeneratedReport.stock_id == stock.id)
            .order_by(GeneratedReport.created_at.desc())
            .limit(min(max(limit, 1), 50))
        ).all()
        return [
            {
                "id": r.id,
                "start_date": r.start_date,
                "end_date": r.end_date,
                "risk_score": r.risk_score,
                "file_path": r.file_path,
                "created_at": r.created_at,
            }
            for r in reports
        ]
    finally:
        db.close()
