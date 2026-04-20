"""
Native desktop launcher — starts Flask in a thread and opens a pywebview window.
Close the window to stop the server.
"""
import threading
import time
import os
import sys

# Make sure we can import app from this directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PORT = 5001


def run_server():
    os.environ['FLASK_DEBUG'] = '0'
    os.environ['HOST'] = '0.0.0.0'
    os.environ['PORT'] = str(PORT)
    from app import app
    app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)


# Start Flask in background thread
t = threading.Thread(target=run_server, daemon=True)
t.start()

# Wait until server is ready
import urllib.request
for _ in range(30):
    try:
        urllib.request.urlopen(f'http://127.0.0.1:{PORT}', timeout=1)
        break
    except Exception:
        time.sleep(0.3)

import webview

window = webview.create_window(
    'IB Revision',
    f'http://127.0.0.1:{PORT}',
    width=1280,
    height=900,
    min_size=(800, 600),
)
webview.start()
# Server thread is daemon so it dies when this process exits
