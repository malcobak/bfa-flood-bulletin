#!/usr/bin/env bash
# Start the bulletin web application (http://127.0.0.1:8000 by default).
#   BULLETIN_PORT=8080 ./run_webapp.sh
#   BULLETIN_HOST=0.0.0.0 ./run_webapp.sh      # also reachable from the local network
#   BULLETIN_CONFIG=/path/config.yaml ./run_webapp.sh
cd "$(dirname "$0")"
# macOS: WeasyPrint (PDF) needs the Pango/GLib libraries installed with Homebrew
if [ "$(uname)" = "Darwin" ]; then
  export DYLD_FALLBACK_LIBRARY_PATH="/opt/homebrew/lib:/usr/local/lib${DYLD_FALLBACK_LIBRARY_PATH:+:$DYLD_FALLBACK_LIBRARY_PATH}"
fi
exec .venv/bin/uvicorn webapp.app:app --host "${BULLETIN_HOST:-127.0.0.1}" --port "${BULLETIN_PORT:-8000}" --proxy-headers
