import argparse
from app.services.importers import import_stock_master_csv


def main():
    parser = argparse.ArgumentParser(description="Import an NSE stock/security master CSV for dashboard discovery.")
    parser.add_argument("csv_path")
    args = parser.parse_args()
    result = import_stock_master_csv(args.csv_path)
    print(f"Catalog records inserted/updated: {result['catalog_records_upserted']:,}")


if __name__ == "__main__":
    main()
