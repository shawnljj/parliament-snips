#!/bin/sh
# Stop the reference/preview servers I started for this task. Keep the "after" build's server
# (8482) alive so the fix can be opened on a phone.
pkill -f "http.server 8483" || true
pkill -f "http.server 8484" || true
sleep 0.5
echo "still listening:"
lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null | grep -E "848[0-9]" || echo "  (none)"
