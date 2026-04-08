#!/bin/bash
set -e

cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "Creating virtual environment..."
  python3.12 -m venv .venv
fi

if [ ! -f ".venv/lib/python3.12/site-packages/fastapi/__init__.py" ]; then
  echo "Installing dependencies..."
  .venv/bin/pip install -q -r requirements.txt
fi

if [ ! -f ".env" ]; then
  echo "No .env found — copying from .env.example"
  cp .env.example .env
fi

exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
