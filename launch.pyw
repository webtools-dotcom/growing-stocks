"""Double-click to open Rising Stocks Terminal in its own window — no terminal.

Starts the Streamlit server hidden, opens it in an app window (Edge/Chrome app mode, no browser tabs or bars)
and stops the server once no window is connected any more. Create a desktop shortcut once with:  python make_shortcut.py

The browser process can't be trusted to signal "window closed": Edge/Chrome often hand the window to an
already-running process and exit at once. So we watch the app's own connections instead.
"""
import os
import shutil
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROFILE = HERE / "data" / "app-window"  # separate browser profile: lets us know when the window closes
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def free_port(start=8501):
    for p in range(start, start + 50):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    raise SystemExit("no free port")


def connections(port):
    """Open TCP connections to our server (the app window keeps a websocket open while it's showing)."""
    out = subprocess.run(["netstat", "-an"], capture_output=True, text=True, creationflags=NO_WINDOW).stdout
    n = 0
    for line in out.splitlines():
        parts = line.split()
        # Windows: proto local foreign state · macOS/Linux: local uses "." or ":" before the port
        if "ESTABLISHED" in line and len(parts) >= 4 and any(p.endswith((f":{port}", f".{port}")) for p in parts[1:2] + parts[3:4]):
            n += 1
    return n


def app_browser():
    candidates = [shutil.which("msedge"), shutil.which("chrome"), shutil.which("google-chrome"), shutil.which("chromium"),
                  os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
                  os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
                  os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
                  os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
                  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    return next((c for c in candidates if c and Path(c).exists()), None)


def main():
    port = free_port()
    python = sys.executable.replace("pythonw.exe", "python.exe")  # pythonw has no stdout for streamlit
    log = open(HERE / "data" / "app.log", "w", encoding="utf-8") if (HERE / "data").exists() else subprocess.DEVNULL
    server = subprocess.Popen([python, "-m", "streamlit", "run", str(HERE / "app.py"), "--server.headless", "true",
                               "--server.port", str(port), "--server.address", "localhost"],
                              cwd=HERE, stdout=log, stderr=subprocess.STDOUT, creationflags=NO_WINDOW,
                              env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    url = f"http://localhost:{port}"
    for _ in range(120):  # wait up to ~60 s for the server
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                break
        if server.poll() is not None:
            raise SystemExit("server failed to start — see data/app.log")
        time.sleep(.5)
    browser = app_browser()
    if browser:
        PROFILE.mkdir(parents=True, exist_ok=True)
        subprocess.Popen([browser, f"--app={url}", f"--user-data-dir={PROFILE}", "--window-size=1440,960",
                          "--no-first-run", "--no-default-browser-check"])
    else:
        webbrowser.open(url)
    try:
        seen, idle, waited = False, 0, 0
        while server.poll() is None:
            if connections(port):
                seen, idle = True, 0
            elif seen:
                idle += 2
            waited += 2
            # window closed (no connection for 12 s), or it never connected within 2 minutes
            if idle >= 12 or (not seen and waited >= 120):
                break
            time.sleep(2)
    finally:
        server.terminate()


if __name__ == "__main__":
    main()
