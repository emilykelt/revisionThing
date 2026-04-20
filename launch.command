#!/bin/bash
# IB Revision — double-click to launch

cd "$(dirname "$0")"

PORT=5001

# Activate virtualenv and start Flask (no reloader so it doesn't flicker on startup)
source venv/bin/activate
FLASK_DEBUG=0 HOST=0.0.0.0 PORT=$PORT python3 app.py &
SERVER_PID=$!

# Wait until the server actually responds (up to 15s)
echo "Starting server..."
READY=0
for i in $(seq 1 30); do
    sleep 0.5
    if curl -s http://127.0.0.1:$PORT > /dev/null 2>&1; then
        READY=1
        break
    fi
done

if [ $READY -eq 0 ]; then
    echo "ERROR: server did not start. Check for errors above."
    wait $SERVER_PID
    exit 1
fi

echo "Server ready."

# Open as standalone window (no browser chrome)
if [ -d "/Applications/Google Chrome.app" ]; then
    open -na "Google Chrome" --args --app=http://127.0.0.1:$PORT --window-size=1280,900 --disable-extensions
elif [ -d "/Applications/Brave Browser.app" ]; then
    open -na "Brave Browser" --args --app=http://127.0.0.1:$PORT --window-size=1280,900
elif [ -d "/Applications/Microsoft Edge.app" ]; then
    open -na "Microsoft Edge" --args --app=http://127.0.0.1:$PORT --window-size=1280,900
else
    # Safari fallback — just opens in a tab
    open "http://127.0.0.1:$PORT"
fi

# Print iPad URL
LOCAL_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)
if [ -n "$LOCAL_IP" ]; then
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  iPad: http://$LOCAL_IP:$PORT"
    echo "  (same WiFi, then Add to Home Screen in Safari)"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
fi

echo "Running. Close this window to stop the server."
wait $SERVER_PID
