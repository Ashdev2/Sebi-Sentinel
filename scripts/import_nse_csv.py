import argparse
from app.services.importers import import_stock_csv


def main():
    parser = argparse.ArgumentParser(description="Import an NSE historical CSV into PostgreSQL.")
    parser.add_argument("csv_path")
    parser.add_argument("--company-name", default=None)
    parser.add_argument("--symbol", default=None, help="Use this when the CSV has no symbol column.")
    args = parser.parse_args()
    result = import_stock_csv(args.csv_path, company_name=args.company_name, forced_symbol=args.symbol)
    print(f"Imported/updated {result['imported_or_updated_rows']:,} price rows from {args.csv_path}.")
    for symbol, info in result["ranges"].items():
        print(f"{symbol}: {info['rows']:,} rows, {info['first_date']} to {info['last_date']}")


if __name__ == "__main__":
    main()
