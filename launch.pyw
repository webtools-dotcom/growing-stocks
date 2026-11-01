"""Double-click to open Rising Stocks Terminal in its own window — no terminal.

Starts the Streamlit server hidden, opens it in an app window (Edge/Chrome app mode, no browser tabs or bars)
and stops the server when that window is closed. Create a desktop shortcut once with:  python make_shortcut.py
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
    try:
        if browser:
            PROFILE.mkdir(parents=True, exist_ok=True)
            subprocess.run([browser, f"--app={url}", f"--user-data-dir={PROFILE}", "--window-size=1440,960",
                            "--no-first-run", "--no-default-browser-check"])  # returns when the window is closed
        else:
            # ponytail: no Chromium browser found — open a normal tab; the server then runs until logoff
            webbrowser.open(url)
            server.wait()
    finally:
        server.terminate()


if __name__ == "__main__":
    main()
