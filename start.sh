#!/usr/bin/env bash
# Simple startup script for deployment platforms that run shell start scripts.
# Make executable: chmod +x start.sh

set -euo pipefail

# Optionally install dependencies (some platforms already handle this)
if [ -f requirements.txt ]; then
  python -m pip install --upgrade pip
  pip install --no-cache-dir -r requirements.txt
fi

# Run the bot using the compatibility entrypoint
python terabox.py
