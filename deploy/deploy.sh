#!/bin/zsh
# Deploy committed code from this Mac to the CCXT-Fr server. See deploy.py for what it does.
#   user_data/deploy/deploy.sh             deploy
#   user_data/deploy/deploy.sh --dry-run   show what would change, touch nothing
exec "$(cd "$(dirname "$0")/../.." && pwd)/.venv/bin/python" "$(dirname "$0")/deploy.py" "$@"
