
## 3.0.3 NSE MCP symbol-parser fix

This build fixes NSE lookup responses where the MCP server returns an aggregate list such as `symbols: ["LTM"]`. Earlier builds could accidentally turn that list into the literal ticker `['LTM']`, causing `get_stock_history` to return no data. The parser now unwraps list/wrapper values and always passes a scalar NSE ticker (for example `LTM` or `HDFCBANK`) to the history tool.

Run the connectivity test with:

```bash
python3.12 -m scripts.test_nse_mcp
```

Expected search output now looks like `LTM | LTM`, not `['LTM'] | ['LTM']`.

# SEBI Sentinel 3.0 — Automated NSE Edition

SEBI Sentinel is an academic/research market-surveillance prototype. This version replaces the manual stock-CSV workflow with **official NSE MCP integration** for normal use.

## Main workflow

1. Type an NSE symbol or company name (for example `LTM`, `RELIANCE`, `TCS`).
2. The backend searches the official NSE Bhavcopy MCP service.
3. Click **Analyze stock**.
4. Missing price history is retrieved automatically and cached in PostgreSQL.
5. The app refreshes the ML model when substantial new history is added.
6. The dashboard renders price/VWAP, volume, surveillance-risk and activity graphs.
7. The Forensics tab explains why the stock/period was flagged.
8. The Investigation Report tab shows the report and creates a PDF.

Manual CSV import still exists under **Settings → Manual CSV fallback**, but it is not required for normal NSE searches.

## Requirements

- macOS or another Python-capable OS
- Python 3.12 (3.10+ should also work)
- PostgreSQL (the existing Postgres.app setup is fine)
- Internet connection for NSE MCP retrieval

## Upgrade from your existing project

Your PostgreSQL data is separate from the code folder, so your existing RELIANCE rows stay in the database.

```bash
cd ~/Desktop/sebi_sentinel_auto
python3.12 -m pip install --user -r requirements.txt
python3.12 -m scripts.init_db
python3.12 -m uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`.

## NSE integration

The app connects to NSE's Streamable-HTTP MCP servers:

- Bhavcopy: `https://mcp.nseindia.in/bhavcopy/cm/mcp`
- CM Market: `https://mcp.nseindia.in/cmmkt/mcp`

The application dynamically discovers the available MCP tools and uses the stock-symbol search, historical-price and quote capabilities. This makes the integration less dependent on hard-coded private web endpoints.

NSE currently advertises the latest **5 years** of Bhavcopy history through its MCP service. The 5Y dashboard range is therefore the automated-history ceiling. Older authorized/licensed history can be imported with the fallback importer.

## Test NSE MCP directly

```bash
python3.12 -m scripts.test_nse_mcp
```

This prints the current tool names and schemas exposed by the official NSE MCP servers. It is the first diagnostic to run if stock search fails.

## Database

Default connection:

```text
postgresql+psycopg://sebi_user:sebi_dev_2026@localhost:5432/sebi_sentinel
```

Override it in `.env` if needed.

## Models

The initial model stack is:

- Isolation Forest
- Explainable surveillance rules

If enough verified official case-period labels are imported, training can also create the known-case classifier. A model alert is a surveillance signal and is not proof that fraud or manipulation occurred.

## PDF report

The PDF includes the selected period, risk score, reason codes, possible event sequence, charts, top anomaly dates, model interpretation and any verified regulatory cases stored for that symbol/period.

## Important research note

SEBI Sentinel is not affiliated with SEBI or NSE. It is an academic/research system. It distinguishes statistical/model anomalies from official regulatory findings.

## 3.0.1 NSE MCP fix
If an older 3.0 build printed `Session termination failed: 501`, that was a non-fatal cleanup response from the NSE MCP server after successful tool calls. Version 3.0.1 also fixes the `get_stock_history` argument/pagination logic: NSE accepts at most 3 months per call, so the app now chains responses backwards automatically for longer ranges.

Test with:

```bash
python3.12 -m scripts.test_nse_mcp
```

The test now verifies both `LTM` symbol search and real historical row retrieval.


## 3.0.2 patch
- Uses `endDate="today"` for the latest NSE Bhavcopy request.
- Retries backwards across current-day/holiday/weekend gaps instead of falsely reporting that a valid symbol has no history.
- NSE MCP test now verifies both LTM and HDFCBANK history retrieval.
