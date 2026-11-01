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
