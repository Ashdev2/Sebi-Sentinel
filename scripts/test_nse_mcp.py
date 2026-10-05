import logging
from datetime import date, timedelta

import anyio

from app.services.nse_mcp import (
    BHAVCOPY_MCP_URL,
    CM_MARKET_MCP_URL,
    fetch_nse_history,
    search_nse_stocks,
)

# NSE's MCP servers currently return HTTP 501 for the optional session-termination
# request made by some MCP client versions. Tool calls still succeed. Silence that
# cleanup warning so this test only reports meaningful failures.
logging.disable(logging.WARNING)


async def show_tools(url: str):
    from mcp import Client

    print(f"Connecting to {url}")
    async with Client(url) as client:
        result = await client.list_tools()
        names = [tool.name for tool in result.tools]
        print(f"  OK - {len(names)} tools available")
        print("  " + ", ".join(names[:12]) + (" ..." if len(names) > 12 else ""))


async def main():
    await show_tools(BHAVCOPY_MCP_URL)
    await show_tools(CM_MARKET_MCP_URL)

    for query in ("LTM", "HDFCBANK"):
        print(f"\nTesting automated symbol search with {query}...")
        matches = await search_nse_stocks(query, limit=8)
        if not matches:
            raise RuntimeError(f"Connected to NSE MCP, but symbol search returned no {query} match.")

        print("  Matches:")
        for item in matches[:5]:
            print(f"   - {item['symbol']} | {item.get('company_name') or item['symbol']}")

        exact = next((item for item in matches if item["symbol"] == query), matches[0])
        symbol = exact["symbol"]

        print(f"\nTesting automated history retrieval for {symbol}...")
        end = date.today()
        start = end - timedelta(days=45)
        rows = await fetch_nse_history(symbol, start, end)
        if not rows:
            raise RuntimeError(f"NSE MCP returned no history for {symbol}.")

        print(f"  OK - received {len(rows)} daily rows")
        print(f"  Range: {rows[0]['trade_date']} -> {rows[-1]['trade_date']}")

    print("\nNSE automation test PASSED.")
    print("The app can search symbols and retrieve history without manual CSV download.")


if __name__ == "__main__":
    anyio.run(main)
