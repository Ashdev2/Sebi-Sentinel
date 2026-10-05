#!/bin/zsh
cd "$(dirname "$0")"
echo "Installing/updating Python packages..."
python3.12 -m pip install --user -r requirements.txt || exit 1
echo "Creating/updating database tables..."
python3.12 -m scripts.init_db || exit 1
echo "Setup complete. Run RUN_SEBI_SENTINEL.command or:"
echo "python3.12 -m uvicorn app.main:app --reload"
