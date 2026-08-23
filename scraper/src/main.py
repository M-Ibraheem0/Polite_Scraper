"""Entry point for the polite scraper.

Stage 1: fetch the first catalogue page once and cache it. On every
later run we read the saved copy instead of asking the site again.
"""

from __future__ import annotations

import sys
from pathlib import Path

import requests

TARGET_URL = "https://books.toscrape.com/catalogue/page-1.html"
USER_AGENT = (
    "FlyRankInternshipA9/1.0 "
    "(+https://github.com/M-Ibraheem0/Polite_Scraper)"
)
TIMEOUT_SECONDS = 10

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_PATH = CACHE_DIR / "catalogue-page-1.html"


def fetch(url: str) -> tuple[int, bytes]:
    """Fetch `url` with a polite user-agent and a hard timeout.

    Returns (status_code, body). Raises requests.RequestException on
    network/timeout errors so the caller can fail loudly.
    """
    response = requests.get(
        url,
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT_SECONDS,
    )
    return response.status_code, response.content


def load_cache(path: Path) -> bytes:
    return path.read_bytes()


def save_cache(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)


def run() -> int:
    if CACHE_PATH.exists():
        body = load_cache(CACHE_PATH)
        source = "CACHE HIT"
        url = str(CACHE_PATH)
    else:
        try:
            status, body = fetch(TARGET_URL)
        except requests.RequestException as exc:
            print(f"FETCH FAILED: {exc}", file=sys.stderr)
            return 1

        if status != 200:
            print(
                f"FETCH FAILED: HTTP {status} for {TARGET_URL}",
                file=sys.stderr,
            )
            return 1

        save_cache(CACHE_PATH, body)
        source = "FETCH"
        url = TARGET_URL

    print(f"{source}: {url}")
    print(f"bytes: {len(body):,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
