#!/usr/bin/env bash
set -euo pipefail

# Usage: ./deploy_fly.sh <app-name>
# Requires: flyctl installed, FLY_API_TOKEN exported, DERIV_APP_ID and DERIV_API_TOKEN exported

APP_NAME="${1:-deriv-bot}"

if ! command -v fly &>/dev/null; then
  echo "Installing flyctl..."
  curl -L https://fly.io/install.sh | sh
  export FLYCTL_INSTALL="$HOME/.fly"
  export PATH="$FLYCTL_INSTALL/bin:$PATH"
fi

: "${FLY_API_TOKEN:?Set FLY_API_TOKEN}"
: "${DERIV_APP_ID:?Set DERIV_APP_ID}"
: "${DERIV_API_TOKEN:?Set DERIV_API_TOKEN}"

# Create app if it doesn't exist
if ! fly apps list | awk '{print $1}' | grep -qx "$APP_NAME"; then
  fly apps create "$APP_NAME" || true
fi

# Set app in fly.toml
sed -i.bak "s/^app = \".*\"/app = \"$APP_NAME\"/" fly.toml || true

# Set secrets
fly secrets set \
  DERIV_APP_ID="$DERIV_APP_ID" \
  DERIV_API_TOKEN="$DERIV_API_TOKEN" \
  STAKE_AMOUNT="${STAKE_AMOUNT:-0.5}" \
  SYMBOLS_CSV="${SYMBOLS_CSV:-1HZ10V,R10,1HZ15V,R25,1HZ25V,1HZ30V,1HZ50V,R50,1HZ75V,R75,1HZ90V,1HZ100V,R100}" \
  RSI_UPPER="${RSI_UPPER:-55}" \
  RSI_LOWER="${RSI_LOWER:-45}" \
  EMA_PERIOD="${EMA_PERIOD:-50}" \
  RSI_PERIOD="${RSI_PERIOD:-2}" \
  ENTRY_SECOND_THRESHOLD="${ENTRY_SECOND_THRESHOLD:-58}" \
  TRADE_DURATION_SECONDS="${TRADE_DURATION_SECONDS:-55}" \
  MAX_SYMBOLS="${MAX_SYMBOLS:-13}" \
  HTF_ENABLED="${HTF_ENABLED:-false}" \
  HTF_GRANULARITY="${HTF_GRANULARITY:-3600}" \
  HTF_FILTER="${HTF_FILTER:-ema}" \
  HTF_EMA_PERIOD="${HTF_EMA_PERIOD:-50}" \
  HTF_RSI_PERIOD="${HTF_RSI_PERIOD:-14}" \
  --app "$APP_NAME"

# Deploy (remote build to avoid local Docker requirement)
fly deploy --remote-only --app "$APP_NAME"

# Scale to 1 process
fly scale count 1 --app "$APP_NAME"

# Tail logs
fly logs --app "$APP_NAME" -f
