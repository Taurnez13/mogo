#!/usr/bin/env bash
# Local / small-office run. Data lives in ./data (SQLite DB, covers, exports).
cd "$(dirname "$0")"
python3 -m venv .venv 2>/dev/null || true
. .venv/bin/activate
pip install -q -r requirements.txt
export SECRET_KEY="${SECRET_KEY:-$(python3 -c 'import secrets;print(secrets.token_hex(32))')}"
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --reload
