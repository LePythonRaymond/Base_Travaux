#!/bin/bash
# Pull the live bordereau into a CSV on the Desktop, then open it in Excel.
# Usage:  ./refresh_bordereau.sh
# Make it executable once: chmod +x refresh_bordereau.sh

set -euo pipefail

API_KEY="9d77b0bca2fe7edcbe2ac839ede91ad3dfa612926bc42e9659161f0a88473b55"
URL="http://127.0.0.1:8765/api/bordereau.csv"
OUT="${HOME}/Desktop/bordereau.csv"

curl -fsS -H "X-API-Key: ${API_KEY}" "${URL}" -o "${OUT}"
LINES=$(wc -l < "${OUT}" | tr -d ' ')
echo "✓ ${OUT} (${LINES} ligne(s))"
open "${OUT}"
