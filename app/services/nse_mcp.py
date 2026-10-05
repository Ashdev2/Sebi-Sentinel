"""Official NSE MCP integration.

This module uses NSE's public Streamable-HTTP MCP endpoints for educational / informational
market-data access. No NSE web-page scraping is used.
"""
from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import DailyPrice, Stock

BHAVCOPY_MCP_URL = "https://mcp.nseindia.in/bhavcopy/cm/mcp"
CM_MARKET_MCP_URL = "https://mcp.nseindia.in/cmmkt/mcp"


class NSEProviderError(RuntimeError):
    pass


@dataclass
class ToolSpec:
    name: str
    title: str
    description: str
    schema: dict[str, Any]


_TOOL_CACHE: dict[str, tuple[datetime, list[ToolSpec]]] = {}
_CACHE_TTL = timedelta(minutes=30)


def _clean_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    text = str(value).replace(",", "").replace("₹", "").replace("%", "").strip()
    if not text or text.lower() in {"na", "n/a", "none", "null", "nan", "-", "--"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _to_int(value: Any) -> int | None:
    number = _to_float(value)
    return None if number is None else int(number)


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    parsed = pd.to_datetime(value, errors="coerce", dayfirst=False)
    if pd.isna(parsed):
        parsed = pd.to_datetime(value, errors="coerce", dayfirst=True)
    return None if pd.isna(parsed) else parsed.date()


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk_dicts(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from _walk_dicts(nested)


def _json_from_text(text: str) -> Any | None:
    text = text.strip()
    candidates = [text]
    if "```" in text:
        for block in re.findall(r"```(?:json)?\s*(.*?)```", text, flags=re.S | re.I):
            candidates.append(block.strip())
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except Exception:
            pass
    # Last chance: locate a JSON object/array in surrounding prose.
    starts = [p for p in [text.find("["), text.find("{")] if p >= 0]
    if starts:
        start = min(starts)
        for end_char in ("]", "}"):
            end = text.rfind(end_char)
            if end > start:
                try:
                    return json.loads(text[start : end + 1])
                except Exception:
                    pass
    return None


def _payload_from_result(result: Any) -> Any:
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        return structured
    texts: list[str] = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            texts.append(text)
    for text in texts:
        parsed = _json_from_text(text)
        if parsed is not None:
            return parsed
    return {"text": "\n".join(texts)}


async def _list_tools(server_url: str) -> list[ToolSpec]:
    cached = _TOOL_CACHE.get(server_url)
    if cached and datetime.now() - cached[0] < _CACHE_TTL:
        return cached[1]

    try:
        from mcp import Client
    except ImportError as exc:
        raise NSEProviderError(
            "The MCP package is not installed. Run: python3.12 -m pip install --user 'mcp>=2.2,<3'"
        ) from exc

    try:
        async with Client(server_url) as client:
            result = await client.list_tools()
            tools = [
                ToolSpec(
                    name=tool.name,
                    title=getattr(tool, "title", None) or "",
                    description=getattr(tool, "description", None) or "",
                    schema=getattr(tool, "input_schema", None) or {},
                )
                for tool in result.tools
            ]
    except Exception as exc:
        raise NSEProviderError(f"Could not connect to the official NSE MCP service: {exc}") from exc

    _TOOL_CACHE[server_url] = (datetime.now(), tools)
    return tools


def _tool_text(tool: ToolSpec) -> str:
    return f"{tool.name} {tool.title} {tool.description}".lower()


def _pick_tool(tools: list[ToolSpec], purpose: str) -> ToolSpec:
    if purpose == "search":
        weighted = [("search", 8), ("symbol", 7), ("stock", 3), ("lookup", 5), ("find", 3)]
        negatives = [("history", -5), ("compare", -4), ("gainer", -4), ("loser", -4)]
    elif purpose == "history":
        weighted = [("history", 10), ("historical", 10), ("ohlcv", 8), ("price", 4), ("stock", 3), ("bhavcopy", 2)]
        negatives = [("compare", -5), ("performance", -3), ("gainer", -5), ("loser", -5), ("search", -3)]
    elif purpose == "quote":
        weighted = [("quote", 10), ("individual", 5), ("stock", 4), ("live", 3), ("equity", 2)]
        negatives = [("bulk", -5), ("history", -5), ("gainer", -4), ("loser", -4)]
    elif purpose == "list_equities":
        weighted = [("all", 3), ("equity", 8), ("stocks", 8), ("listing", 8), ("list", 6)]
        negatives = [("quote", -3), ("history", -5), ("gainer", -5), ("loser", -5), ("bond", -6), ("sme", -4)]
    else:
        raise ValueError(purpose)

    scored: list[tuple[int, ToolSpec]] = []
    for tool in tools:
        text = _tool_text(tool)
        score = sum(weight for token, weight in weighted if token in text)
        score += sum(weight for token, weight in negatives if token in text)
        scored.append((score, tool))
    scored.sort(key=lambda item: item[0], reverse=True)
    if not scored or scored[0][0] <= 0:
        raise NSEProviderError(
            f"NSE MCP did not expose a suitable {purpose} tool. Available tools: "
            + ", ".join(tool.name for tool in tools)
        )
    return scored[0][1]


def _properties(tool: ToolSpec) -> dict[str, Any]:
    return tool.schema.get("properties", {}) if isinstance(tool.schema, dict) else {}


def _default_for_schema(schema: dict[str, Any]) -> Any:
    if "default" in schema:
        return schema["default"]
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        if "EQ" in enum:
            return "EQ"
        return enum[0]
    typ = schema.get("type")
    if typ in {"integer", "number"}:
        # Respect MCP tool bounds. This matters for NSE get_stock_history,
        # where `months` is required and has a maximum of 3.
        value = 20
        maximum = schema.get("maximum")
        minimum = schema.get("minimum")
        if maximum is not None:
            value = min(value, maximum)
        if minimum is not None:
            value = max(value, minimum)
        return int(value) if typ == "integer" else value
    if typ == "boolean":
        return False
    if typ == "array":
        return []
    return ""


def _find_prop(props: dict[str, Any], exact: tuple[str, ...], contains: tuple[str, ...]) -> str | None:
    cleaned = {key: _clean_key(key) for key in props}
    for key, value in cleaned.items():
        if value in exact:
            return key
    for token in contains:
        for key, value in cleaned.items():
            if token in value:
                return key
    return None


def _fill_required(tool: ToolSpec, args: dict[str, Any]) -> dict[str, Any]:
    props = _properties(tool)
    for key in tool.schema.get("required", []) if isinstance(tool.schema, dict) else []:
        if key not in args:
            args[key] = _default_for_schema(props.get(key, {}))
    return args


def _search_args(tool: ToolSpec, query: str, limit: int) -> dict[str, Any]:
    props = _properties(tool)
    query_key = _find_prop(
        props,
        ("query", "search", "keyword", "term", "symbol", "name"),
        ("query", "search", "keyword", "term", "symbol", "company", "name"),
    )
    if not query_key:
        raise NSEProviderError(f"Could not infer the query argument for NSE MCP tool '{tool.name}'.")
    args: dict[str, Any] = {query_key: query}
    limit_key = _find_prop(props, ("limit", "max items", "count"), ("limit", "max", "count"))
    if limit_key:
        args[limit_key] = limit
    return _fill_required(tool, args)


def _history_args(tool: ToolSpec, symbol: str, start: date, end: date, date_style: str = "iso") -> dict[str, Any]:
    props = _properties(tool)
    symbol_key = _find_prop(
        props,
        ("symbol", "ticker", "security", "stock"),
        ("symbol", "ticker", "security", "stock"),
    )
    if not symbol_key:
        raise NSEProviderError(f"Could not infer the symbol argument for NSE MCP tool '{tool.name}'.")

    def date_text(value: date) -> str:
        if date_style == "dmy":
            return value.strftime("%d-%m-%Y")
        if date_style == "compact":
            return value.strftime("%Y%m%d")
        return value.isoformat()

    args: dict[str, Any] = {symbol_key: symbol.upper()}
    start_key = _find_prop(
        props,
        ("from date", "start date", "from", "start"),
        ("from date", "start date", "from", "start"),
    )
    end_key = _find_prop(
        props,
        ("to date", "end date", "to", "end"),
        ("to date", "end date", "to", "end"),
    )
    if start_key:
        args[start_key] = date_text(start)
    if end_key:
        args[end_key] = date_text(end)

    series_key = _find_prop(props, ("series",), ("series",))
    if series_key:
        schema = props.get(series_key, {})
        args[series_key] = ["EQ"] if schema.get("type") == "array" else "EQ"

    limit_key = _find_prop(props, ("limit", "max items", "count"), ("max items", "limit", "count"))
    if limit_key:
        schema = props.get(limit_key, {})
        args[limit_key] = min(5000, schema.get("maximum", 5000) or 5000)

    return _fill_required(tool, args)


def _quote_args(tool: ToolSpec, symbol: str) -> dict[str, Any]:
    props = _properties(tool)
    symbol_key = _find_prop(props, ("symbol", "ticker", "security", "stock"), ("symbol", "ticker", "security", "stock"))
    if not symbol_key:
        raise NSEProviderError(f"Could not infer the symbol argument for NSE MCP tool '{tool.name}'.")
    return _fill_required(tool, {symbol_key: symbol.upper()})


async def _call(server_url: str, tool: ToolSpec, arguments: dict[str, Any]) -> Any:
    from mcp import Client

    async with Client(server_url) as client:
        result = await client.call_tool(tool.name, arguments)
    if getattr(result, "is_error", False):
        payload = _payload_from_result(result)
        raise NSEProviderError(f"NSE MCP tool '{tool.name}' returned an error: {payload}")
    return _payload_from_result(result)


def _normalized_record(record: dict[str, Any]) -> dict[str, Any]:
    return {_clean_key(key): value for key, value in record.items()}


def _value(record: dict[str, Any], *aliases: str) -> Any:
    normalized = _normalized_record(record)
    for alias in aliases:
        key = _clean_key(alias)
        if key in normalized:
            return normalized[key]
    # Fuzzy fallback for verbose exchange field names.
    for alias in aliases:
        token = _clean_key(alias)
        for key, value in normalized.items():
            if token and token in key:
                return value
    return None


def _flatten_symbol_values(value: Any) -> list[str]:
    """Return clean ticker strings from NSE MCP scalar/list/wrapper values.

    Some NSE MCP responses expose lookup results as ``symbols: ["LTM"]``.
    The older parser fuzzily matched the plural key and converted the whole list
    to the literal string ``"['LTM']"``.  That malformed ticker was then sent
    to ``get_stock_history``.
    """
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        # Also recover strings that already look like a Python/JSON list.
        if text[:1] in {"[", "("} and text[-1:] in {"]", ")"}:
            try:
                parsed = ast.literal_eval(text)
            except Exception:
                parsed = None
            if parsed is not None and parsed is not value:
                return _flatten_symbol_values(parsed)
        return [text.upper()]
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            out.extend(_flatten_symbol_values(item))
        return out
    if isinstance(value, dict):
        out: list[str] = []
        for key in ("symbol", "symbols", "ticker", "tickers", "value", "values"):
            if key in value:
                out.extend(_flatten_symbol_values(value[key]))
        return out
    return [str(value).strip().upper()] if str(value).strip() else []


def _scalar_text(value: Any) -> str | None:
    """Best-effort scalar text; avoids turning lists into Python repr strings."""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, (list, tuple)):
        if len(value) == 1:
            return _scalar_text(value[0])
        return None
    return str(value).strip() or None


def _extract_search_results(payload: Any, query: str) -> list[dict[str, Any]]:
    query_upper = query.upper().strip()
    candidates: list[dict[str, Any]] = []

    for record in _walk_dicts(payload):
        # Prefer exact scalar symbol/ticker fields.  Do not let fuzzy matching of
        # a plural aggregate field (``symbols``) become the literal ticker
        # ``['LTM']``.
        normalized = _normalized_record(record)
        raw_symbol = None
        for key in ("symbol", "stock symbol", "security symbol", "ticker", "nse symbol"):
            if key in normalized:
                raw_symbol = normalized[key]
                break

        symbols = _flatten_symbol_values(raw_symbol)
        if symbols:
            company = _scalar_text(_value(record, "company name", "security name", "name", "company", "issuer"))
            isin = _scalar_text(_value(record, "isin", "isin number"))
            for symbol in symbols:
                symbol = symbol.strip().upper()
                if not symbol or len(symbol) > 30 or not re.fullmatch(r"[A-Z0-9&._-]+", symbol):
                    continue
                candidates.append({
                    "symbol": symbol,
                    "company_name": company or symbol,
                    "isin": isin,
                    "source": "NSE official MCP",
                })

        # NSE's light lookup tool can return an aggregate plural field such as
        # {"symbols": ["LTM"]}. Handle that explicitly.
        for plural_key in ("symbols", "tickers", "results", "matches"):
            if plural_key not in normalized:
                continue
            for symbol in _flatten_symbol_values(normalized[plural_key]):
                symbol = symbol.strip().upper()
                if not symbol or len(symbol) > 30 or not re.fullmatch(r"[A-Z0-9&._-]+", symbol):
                    continue
                candidates.append({
                    "symbol": symbol,
                    "company_name": symbol,
                    "isin": None,
                    "source": "NSE official MCP",
                })

    # Last fallback for top-level lists of symbol strings.
    if not candidates:
        for symbol in _flatten_symbol_values(payload):
            symbol = symbol.strip().upper()
            if symbol and len(symbol) <= 30 and re.fullmatch(r"[A-Z0-9&._-]+", symbol):
                candidates.append({
                    "symbol": symbol,
                    "company_name": symbol,
                    "isin": None,
                    "source": "NSE official MCP",
                })

    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for candidate in candidates:
        symbol = candidate["symbol"]
        if symbol in seen:
            # Preserve the richer company name if a later record has one.
            for existing in unique:
                if existing["symbol"] == symbol and existing["company_name"] == symbol and candidate["company_name"] != symbol:
                    existing.update(candidate)
                    break
            continue
        seen.add(symbol)
        unique.append(candidate)

    def rank(item: dict[str, Any]) -> tuple[int, int, str]:
        symbol = item["symbol"].upper()
        company = item["company_name"].upper()
        exact = 0 if symbol == query_upper else 1
        contains = 0 if query_upper in symbol or query_upper in company else 1
        return (exact, contains, symbol)

    return sorted(unique, key=rank)


def _extract_history_rows(payload: Any, symbol: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in _walk_dicts(payload):
        trade_date = _parse_date(_value(record, "date", "trade date", "trading date", "timestamp", "ch timestamp", "m timestamp"))
        close = _to_float(_value(record, "close", "close price", "closing price", "ch closing price", "last price", "ltp"))
        if trade_date is None or close is None:
            continue
        rows.append({
            "symbol": str(_value(record, "symbol", "stock symbol", "security symbol", "ticker") or symbol).strip().upper(),
            "trade_date": trade_date,
            "series": str(_value(record, "series") or "EQ").strip().upper(),
            "previous_close": _to_float(_value(record, "previous close", "prev close", "ch previous cls price")),
            "open_price": _to_float(_value(record, "open", "open price", "ch opening price")),
            "high_price": _to_float(_value(record, "high", "high price", "ch trade high price")),
            "low_price": _to_float(_value(record, "low", "low price", "ch trade low price")),
            "last_price": _to_float(_value(record, "last", "last price", "last traded price", "ltp")),
            "close_price": close,
            "vwap": _to_float(_value(record, "vwap", "average price", "weighted average price")),
            "volume": _to_int(_value(record, "volume", "total traded quantity", "traded quantity", "total volume", "ch tot traded qty")),
            "turnover": _to_float(_value(record, "turnover", "traded value", "value")),
            "number_of_trades": _to_int(_value(record, "number of trades", "no of trades", "trades", "total trades")),
            "delivery_quantity": _to_int(_value(record, "deliverable quantity", "delivery quantity", "deliverable qty")),
            "delivery_percentage": _to_float(_value(record, "delivery percentage", "deliverable percent", "delivery pct", "dly qt to traded qty")),
        })
    # Deduplicate by date/series, preserving the last record.
    dedup: dict[tuple[date, str], dict[str, Any]] = {}
    for row in rows:
        dedup[(row["trade_date"], row["series"])] = row
    return sorted(dedup.values(), key=lambda row: row["trade_date"])


async def search_nse_stocks(query: str, limit: int = 12) -> list[dict[str, Any]]:
    query = query.strip()
    if not query:
        return []

    primary_error = None
    try:
        tools = await _list_tools(BHAVCOPY_MCP_URL)
        tool = _pick_tool(tools, "search")
        payload = await _call(BHAVCOPY_MCP_URL, tool, _search_args(tool, query, limit))
        results = _extract_search_results(payload, query)[:limit]
        if results:
            return results
    except Exception as exc:
        primary_error = exc

    # Fallback to the official CM Market "all equity stocks listing" capability and filter locally.
    try:
        tools = await _list_tools(CM_MARKET_MCP_URL)
        tool = _pick_tool(tools, "list_equities")
        args = _fill_required(tool, {})
        payload = await _call(CM_MARKET_MCP_URL, tool, args)
        results = _extract_search_results(payload, query)
        q = query.upper()
        results = [r for r in results if q in r["symbol"].upper() or q in r["company_name"].upper()]
        return results[:limit]
    except Exception as exc:
        if primary_error:
            raise NSEProviderError(f"NSE stock search failed on both official MCP services. Bhavcopy: {primary_error}; CM Market: {exc}") from exc
        raise


def _extract_next_end_date(payload: Any) -> date | None:
    """Find NSE get_stock_history pagination cursor in a structured MCP response."""
    aliases = {
        "next end date",
        "next_end_date",
        "nextenddate",
        "next enddate",
    }
    for record in _walk_dicts(payload):
        normalized = {_clean_key(key): value for key, value in record.items()}
        for key, value in normalized.items():
            compact = key.replace(" ", "")
            if key in aliases or compact == "nextenddate":
                parsed = _parse_date(value)
                if parsed is not None:
                    return parsed
    return None


async def fetch_nse_history(symbol: str, start: date, end: date) -> list[dict[str, Any]]:
    """Fetch up to 5 years of NSE daily OHLCV by chaining 3-month MCP chunks.

    Important NSE behavior:
    - Bhavcopy is end-of-day data and the latest historical bar is normally the previous trading day.
    - The MCP schema explicitly accepts ``endDate="today"`` for the latest available history.
    - If a requested end date is a holiday/weekend or today's bhavcopy is not ready yet,
      retry a few earlier calendar dates instead of treating the stock as missing.
    """
    # Defensive normalization in case a caller passes an MCP lookup wrapper
    # such as ["LTM"] instead of the ticker scalar.
    normalized_symbols = _flatten_symbol_values(symbol)
    if not normalized_symbols:
        raise NSEProviderError("A valid NSE symbol is required for historical data.")
    symbol = normalized_symbols[0]

    if end < start:
        raise NSEProviderError("End date must be on or after start date.")

    today = date.today()
    earliest_supported = today - timedelta(days=366 * 5 + 10)
    start = max(start, earliest_supported)
    end = min(end, today)

    tools = await _list_tools(BHAVCOPY_MCP_URL)
    tool = next((item for item in tools if item.name == "get_stock_history"), None) or _pick_tool(tools, "history")
    props = _properties(tool)

    symbol_key = _find_prop(
        props,
        ("symbol", "ticker", "security", "stock"),
        ("symbol", "ticker", "security", "stock"),
    )
    months_key = _find_prop(props, ("months", "month"), ("month",))
    end_key = _find_prop(props, ("end date", "to date", "end"), ("end date", "enddate", "to date", "end"))

    if not symbol_key or not months_key or not end_key:
        raise NSEProviderError(
            f"Unexpected NSE history schema for '{tool.name}'. Expected symbol/months/endDate; got {list(props)}"
        )

    collected: dict[tuple[date, str], dict[str, Any]] = {}
    current_end = end
    previous_end: date | None = None
    calls = 0
    max_calls = 28  # five years plus room for holiday/current-day retries
    empty_retries = 0
    max_empty_retries = 8

    while current_end >= start and calls < max_calls:
        calls += 1

        # NSE's tool documentation says to use the literal string "today" for
        # the latest available Bhavcopy history. This avoids asking for an
        # incomplete current-day EOD file during market hours.
        end_arg = "today" if current_end >= today else current_end.isoformat()

        args = {
            symbol_key: symbol.upper(),
            months_key: 3,
            end_key: end_arg,
        }
        args = _fill_required(tool, args)

        try:
            payload = await _call(BHAVCOPY_MCP_URL, tool, args)
        except Exception as exc:
            # A date can be unavailable because it is a holiday/weekend or the
            # latest EOD dataset is not published yet. Retry backwards before
            # declaring that the symbol has no history.
            if empty_retries < max_empty_retries:
                empty_retries += 1
                current_end = current_end - timedelta(days=1)
                continue
            raise NSEProviderError(
                f"NSE history request failed for {symbol.upper()} ending {current_end.isoformat()}: {exc}"
            ) from exc

        chunk_rows = _extract_history_rows(payload, symbol)
        if not chunk_rows:
            if empty_retries < max_empty_retries:
                empty_retries += 1
                current_end = current_end - timedelta(days=1)
                continue
            break

        empty_retries = 0

        for row in chunk_rows:
            trade_date = row["trade_date"]
            if start <= trade_date <= end:
                collected[(trade_date, row.get("series") or "EQ")] = row

        earliest_chunk_date = min(row["trade_date"] for row in chunk_rows)
        if earliest_chunk_date <= start:
            break

        next_end = _extract_next_end_date(payload)
        if next_end is None:
            next_end = earliest_chunk_date - timedelta(days=1)

        # Safety against a provider cursor that doesn't move backwards.
        if next_end >= current_end or next_end == previous_end:
            next_end = earliest_chunk_date - timedelta(days=1)
        if next_end >= current_end:
            break

        previous_end = current_end
        current_end = next_end

    rows = sorted(collected.values(), key=lambda row: row["trade_date"])
    if not rows:
        raise NSEProviderError(
            f"NSE MCP returned no usable price history for {symbol.upper()} between {start} and {end}. "
            "The Bhavcopy service is end-of-day data; if this is the current trading day, "
            "the app also retrieves today's live snapshot separately from CM Market."
        )
    return rows


async def fetch_live_quote(symbol: str) -> dict[str, Any] | None:
    try:
        tools = await _list_tools(CM_MARKET_MCP_URL)
        tool = _pick_tool(tools, "quote")
        payload = await _call(CM_MARKET_MCP_URL, tool, _quote_args(tool, symbol))
        # Return a compact normalized quote if possible, otherwise raw structured payload.
        for record in _walk_dicts(payload):
            rec_symbol = _value(record, "symbol", "stock symbol", "security symbol", "ticker")
            if rec_symbol and str(rec_symbol).strip().upper() != symbol.upper():
                continue
            last = _to_float(_value(record, "last price", "ltp", "last traded price", "price", "close"))
            if last is not None:
                return {
                    "symbol": symbol.upper(),
                    "last_price": last,
                    "change_percent": _to_float(_value(record, "change percent", "p change", "percent change", "change %")),
                    "open": _to_float(_value(record, "open", "open price")),
                    "high": _to_float(_value(record, "high", "day high", "high price")),
                    "low": _to_float(_value(record, "low", "day low", "low price")),
                    "volume": _to_int(_value(record, "volume", "total traded volume", "total traded quantity")),
                    "source": "NSE official MCP",
                }
    except Exception:
        return None
    return None


def upsert_nse_history(symbol: str, company_name: str | None, isin: str | None, rows: list[dict[str, Any]]) -> dict[str, Any]:
    symbol = symbol.upper().strip()
    db = SessionLocal()
    imported = 0
    inserted = 0
    try:
        stock = db.scalar(select(Stock).where(Stock.symbol == symbol))
        if stock is None:
            stock = Stock(symbol=symbol, company_name=company_name or symbol, isin=isin)
            db.add(stock)
            db.flush()
        else:
            if company_name and company_name != symbol:
                stock.company_name = company_name
            if isin:
                stock.isin = isin

        for row in rows:
            series = row.get("series") or "EQ"
            existing = db.scalar(
                select(DailyPrice).where(
                    DailyPrice.stock_id == stock.id,
                    DailyPrice.trade_date == row["trade_date"],
                    DailyPrice.series == series,
                )
            )
            values = {key: row.get(key) for key in [
                "previous_close", "open_price", "high_price", "low_price", "last_price", "close_price",
                "vwap", "volume", "turnover", "number_of_trades", "delivery_quantity", "delivery_percentage",
            ]}
            values["source_file"] = "NSE official MCP"
            if existing:
                for key, value in values.items():
                    if value is not None or key == "source_file":
                        setattr(existing, key, value)
            else:
                db.add(DailyPrice(stock_id=stock.id, trade_date=row["trade_date"], series=series, **values))
                inserted += 1
            imported += 1
        db.commit()

        total, first_date, last_date = db.execute(
            select(func.count(DailyPrice.id), func.min(DailyPrice.trade_date), func.max(DailyPrice.trade_date))
            .where(DailyPrice.stock_id == stock.id)
        ).one()
        return {
            "symbol": stock.symbol,
            "company_name": stock.company_name,
            "rows_received": len(rows),
            "rows_upserted": imported,
            "new_rows": inserted,
            "total_rows": int(total or 0),
            "first_date": first_date,
            "last_date": last_date,
            "source": "NSE official MCP",
        }
    finally:
        db.close()


async def ensure_nse_stock_history(symbol_or_name: str, start: date, end: date) -> dict[str, Any]:
    query = symbol_or_name.strip()
    if not query:
        raise NSEProviderError("Enter an NSE symbol or company name.")

    search_results = await search_nse_stocks(query, limit=15)
    if not search_results:
        raise NSEProviderError(f"NSE did not return a stock matching '{query}'.")

    query_upper = query.upper()
    exact = next((item for item in search_results if item["symbol"] == query_upper), None)
    chosen = exact or search_results[0]
    symbol = chosen["symbol"]

    db = SessionLocal()
    try:
        stock = db.scalar(select(Stock).where(Stock.symbol == symbol))
        if stock is not None:
            count, first_date, last_date = db.execute(
                select(func.count(DailyPrice.id), func.min(DailyPrice.trade_date), func.max(DailyPrice.trade_date))
                .where(DailyPrice.stock_id == stock.id)
            ).one()
        else:
            count, first_date, last_date = 0, None, None
    finally:
        db.close()

    # Refresh if coverage is missing at either edge or the latest day is stale by > 4 calendar days.
    today = date.today()
    requested_start = max(start, today - timedelta(days=366 * 5 + 10))
    needs_sync = not count or first_date is None or last_date is None or first_date > requested_start or last_date < min(end, today - timedelta(days=4))

    sync_result: dict[str, Any] | None = None
    if needs_sync:
        rows = await fetch_nse_history(symbol, requested_start, min(end, today))
        sync_result = upsert_nse_history(symbol, chosen.get("company_name"), chosen.get("isin"), rows)
    else:
        sync_result = {
            "symbol": symbol,
            "company_name": stock.company_name if stock else chosen.get("company_name") or symbol,
            "new_rows": 0,
            "total_rows": int(count or 0),
            "first_date": first_date,
            "last_date": last_date,
            "source": "PostgreSQL cache (NSE MCP sourced)",
        }

    sync_result["search_match"] = chosen
    sync_result["requested_start"] = requested_start
    sync_result["requested_end"] = end
    sync_result["history_limit_note"] = "Official NSE Bhavcopy MCP currently advertises the latest 5 years of historical data."
    return sync_result
