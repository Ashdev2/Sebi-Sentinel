import argparse
from datetime import date

from app.reports.generator import create_report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    args = parser.parse_args()

    output = create_report(args.symbol, date.fromisoformat(args.start), date.fromisoformat(args.end))
    print(f"PDF generated successfully:\n{output}")


if __name__ == "__main__":
    main()
