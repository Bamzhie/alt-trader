#!/bin/sh
# Start the ALT RADAR mock service (DEV ONLY — static fixtures, not live data).
#
# The service binds 127.0.0.1 and serves the documented HTTP+SSE contract so
# the React app can be developed and tested before proto/service.py exists.
# The dev bearer token is printed once to stdout.
#
#   bash app/dev/serve.sh                 # port 18741
#   PORT=18999 bash app/dev/serve.sh      # override the port
set -eu

DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

exec python3 "$DIR/mock_service.py" --port "${PORT:-18741}" "$@"
