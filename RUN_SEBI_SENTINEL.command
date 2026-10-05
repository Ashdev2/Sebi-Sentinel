#!/bin/zsh
cd "$(dirname "$0")"
echo "Starting SEBI Sentinel at http://127.0.0.1:8000"
python3.12 -m uvicorn app.main:app --reload
