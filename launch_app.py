"""
Native desktop launcher — starts Flask in a thread and opens a pywebview window.
Close the window to stop the server.
"""
import threading
import time
import os
import sys
import socket
import signal

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PORT = 5001


def _kill_port(port):
    """Kill any process already using the port so we can bind cleanly."""
    import subprocess
    try:
        result = subprocess.run(
            ['lsof', '-ti', f':{port}'],
            capture_output=True, text=True
        )
        pids = result.stdout.strip().split()
        for pid in pids:
            try:
                os.kill(int(pid), signal.SIGTERM)
            except (ProcessLookupError, ValueError):
                pass
        if pids:
            time.sleep(0.4)
    except Exception:
        pass


def _wait_for_port(port, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=0.2):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def run_server():
    os.environ['FLASK_DEBUG'] = '0'
    os.environ['HOST'] = '0.0.0.0'
    os.environ['PORT'] = str(PORT)
    from app import app
    app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)


# Free the port if something is squatting on it
_kill_port(PORT)

# Start Flask in background thread
t = threading.Thread(target=run_server, daemon=True)
t.start()

# Wait until server is ready (fast socket poll, 10s max)
ready = _wait_for_port(PORT, timeout=10.0)

import webview

if not ready:
    # Show an error window instead of silently doing nothing
    w = webview.create_window(
        'IB Revision — Error',
        html='<body style="font:16px sans-serif;padding:2rem"><h2>Server failed to start</h2>'
             '<p>Check that the virtual environment is set up correctly.</p></body>',
        width=480, height=200,
    )
else:
    w = webview.create_window(
        'IB Revision',
        f'http://127.0.0.1:{PORT}',
        width=1280,
        height=900,
        min_size=(800, 600),
    )

webview.start()
