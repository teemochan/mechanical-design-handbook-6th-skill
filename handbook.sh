#!/usr/bin/env bash
set -euo pipefail
KB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -x "$KB_DIR/.venv/bin/python" ]; then
    exec "$KB_DIR/.venv/bin/python" "$KB_DIR/scripts/handbook_cli.py" "$@"
else
    exec python3 "$KB_DIR/scripts/handbook_cli.py" "$@"
fi
