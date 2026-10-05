import argparse
from datetime import date

from app.services.analysis import analyze_stock


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    args = parser.parse_args()

    stock, df, meta = analyze_stock(args.symbol, date.fromisoformat(args.start), date.fromisoformat(args.end))
    print(f"\n{stock.company_name} ({stock.symbol})")
    print(f"Rows in period: {len(df):,}")
    print("Model stack:", ", ".join(meta["model_stack"]), "\n")
    if df.empty:
        print("No data in the selected period.")
        return

    columns = ["trade_date", "close_price", "volume_ratio_20d", "anomaly_score", "case_score", "rule_score", "risk_score", "risk_level", "suspected_pattern"]
    print(df[columns].sort_values("risk_score", ascending=False).head(20).to_string(index=False))


if __name__ == "__main__":
    main()
