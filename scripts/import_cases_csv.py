import argparse
from app.services.importers import import_cases_csv


def main():
    parser = argparse.ArgumentParser(description="Import manually verified official regulatory cases.")
    parser.add_argument("csv_path")
    args = parser.parse_args()
    result = import_cases_csv(args.csv_path)
    print(f"Imported {result['regulatory_cases_imported']} regulatory case records.")


if __name__ == "__main__":
    main()
