"""Entry point for the polite scraper.

Stage 2: discover all three catalogue pages by following the catalogue's
own "next" link, collect the absolute URL of every book on those pages,
deduplicate, and report the totals. Cached pages never touch the network;
real fetches are spaced by DELAY_SECONDS.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "FlyRankInternshipA9/1.0 "
    "(+https://github.com/M-Ibraheem0/Polite_Scraper)"
)
TIMEOUT_SECONDS = 10
DELAY_SECONDS = 0.5
MAX_PAGES = 3
CATALOGUE_START = "https://books.toscrape.com/catalogue/page-1.html"

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"


def fetch(url: str) -> tuple[int, bytes]:
    response = requests.get(
        url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS
    )
    return response.status_code, response.content


def cache_path_for(url: str) -> Path:
    """Map a catalogue URL to a stable local cache file.

    https://books.toscrape.com/catalogue/page-2.html -> cache/page-2.html
    """
    return CACHE_DIR / url.rsplit("/", 1)[-1]


def get_page(url: str) -> tuple[bytes, str]:
    """Return (body, 'CACHE HIT' | 'FETCH') for `url`."""
    path = cache_path_for(url)
    if path.exists():
        return path.read_bytes(), "CACHE HIT"

    status, body = fetch(url)
    if status != 200:
        raise RuntimeError(f"HTTP {status} for {url}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return body, "FETCH"


def parse_catalogue(html: bytes, page_url: str) -> tuple[list[str], str | None]:
    """Return (absolute book URLs on this page, absolute next page URL or None)."""
    soup = BeautifulSoup(html, "html.parser")

    book_urls: list[str] = []
    for article in soup.select("article.product_pod h3 a"):
        href = article.get("href")
        if href:
            book_urls.append(urljoin(page_url, href))

    next_url: str | None = None
    next_a = soup.select_one("li.next a")
    if next_a and next_a.get("href"):
        next_url = urljoin(page_url, next_a["href"])

    return book_urls, next_url


def run() -> int:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    page_url: str | None = CATALOGUE_START
    all_books: list[str] = []
    pages_visited = 0
    last_was_network = False

    for _ in range(MAX_PAGES):
        if page_url is None:
            break
        if last_was_network:
            time.sleep(DELAY_SECONDS)

        try:
            html, source = get_page(page_url)
        except (requests.RequestException, RuntimeError) as exc:
            print(f"FETCH FAILED: {exc}", file=sys.stderr)
            return 1

        last_was_network = source == "FETCH"

        book_urls, next_url = parse_catalogue(html, page_url)
        all_books.extend(book_urls)
        pages_visited += 1
        print(f"{source}: {page_url} -> {len(book_urls)} books")

        page_url = next_url

    unique_urls = list(dict.fromkeys(all_books))

    print(f"catalogue_pages={pages_visited}")
    print(f"discovered={len(all_books)}")
    print(f"unique_urls={len(unique_urls)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
