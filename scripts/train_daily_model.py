from app.services.training import train_models


def main():
    result = train_models()
    print(f"Stocks used: {result['stocks_used']}")
    print(f"Anomaly training rows: {result['anomaly_training_rows']:,}")
    print(f"Anomaly model saved: {result['anomaly_model']}")
    if result["case_classifier_created"]:
        print(f"Known-case classifier saved: {result['case_classifier']}")
        print(f"Case training rows: {result['case_training_rows']:,} (positive={result['case_positive_rows']:,}, negative={result['case_negative_rows']:,})")
    else:
        print("Known-case classifier not created yet: add enough verified regulatory case-period rows first.")


if __name__ == "__main__":
    main()
