"""Paths and shared helpers."""
from pathlib import Path

import requests
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

DATA = HERE / "data"
DATA.mkdir(exist_ok=True)

import settings  # noqa: E402  (after .env is loaded: env vars override saved settings)

settings.apply()
DB = DATA / "market.duckdb"  # built by data_sync.py from public NSE files + Screener.in


def open_db(read_only=True, wait=180):
    """Open the market DB, waiting up to `wait` seconds if another process has it. On Windows DuckDB allows one
    process at a time while the file is being written (update button, scheduled update, the app reading), so a
    plain connect fails at once with "being used by another process"; this waits its turn instead."""
    import time

    import duckdb
    end = time.time() + wait
    while True:
        try:
            return duckdb.connect(str(DB), read_only=read_only)
        except duckdb.IOException as e:
            busy = "being used by another process" in str(e) or "lock" in str(e).lower()
            if not busy or time.time() >= end:
                raise
            time.sleep(1)


def nse_session():
    """requests session with the browser headers + cookies NSE's JSON APIs require."""
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.nseindia.com/",
    })
    s.get("https://www.nseindia.com", timeout=15)
    return s
