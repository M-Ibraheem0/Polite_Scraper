"""Entry point for the polite scraper.

Stage 3: open every book detail page discovered on the first three
catalogue pages, extract the eight-field raw record, and write the
collection to data/raw/books.json. Cached pages never touch the
network; real fetches are spaced by DELAY_SECONDS.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

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
RAW_OUTPUT = Path(__file__).resolve().parent.parent / "data" / "raw" / "books.json"


def fetch(url: str) -> tuple[int, bytes]:
    response = requests.get(
        url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS
    )
    return response.status_code, response.content


def cache_path_for(url: str) -> Path:
    """Map a URL to a unique local cache file inside CACHE_DIR.

    Examples:
      .../catalogue/page-1.html              -> cache/catalogue_page-1.html
      .../catalogue/a-light-in-the-attic_1000/index.html
                                             -> cache/catalogue_a-light-in-the-attic_1000_index.html
    """
    path = urlparse(url).path.strip("/")
    return CACHE_DIR / path.replace("/", "_")


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


def parse_book_page(html: bytes, book_url: str, source_page: str) -> dict:
    """Extract the raw 8-field record from a book detail page.

    Selectors are scoped to the product area (div.product_main) so a
    future page redesign that adds a second price elsewhere cannot
    silently swap our values. `description` is null when the page does
    not provide one — we never invent text.
    """
    soup = BeautifulSoup(html, "html.parser")
    product = soup.select_one("div.product_main")
    if product is None:
        raise RuntimeError(f"div.product_main not found on {book_url}")

    title_el = product.select_one("h1")
    price_el = product.select_one("p.price_color")
    avail_el = product.select_one("p.instock.availability")
    rating_el = product.select_one("p.star-rating")

    rating_text: str | None = None
    if rating_el is not None:
        for cls in rating_el.get("class", []):
            if cls != "star-rating":
                rating_text = cls
                break

    description: str | None = None
    desc_anchor = soup.select_one("#product_description")
    if desc_anchor is not None:
        next_p = desc_anchor.find_next("p")
        if next_p is not None:
            text = next_p.get_text(strip=True)
            if text:
                description = text

    return {
        "title": title_el.get_text(strip=True) if title_el else None,
        "product_url": book_url,
        "price_text": price_el.get_text(strip=True) if price_el else None,
        "availability_text": (
            avail_el.get_text(strip=True) if avail_el else None
        ),
        "rating_text": rating_text,
        "description": description,
        "source_page": source_page,
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def discover_books() -> tuple[list[tuple[str, str]], bool]:
    """Walk the first MAX_PAGES catalogue pages.

    Returns (unique_books, ok) where each entry is (book_url, source_page)
    and the bool reports whether any catalogue request failed.
    """
    discovered: list[tuple[str, str]] = []
    page_url: str | None = CATALOGUE_START
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
            return [], False
        last_was_network = source == "FETCH"
        book_links, next_url = parse_catalogue(html, page_url)
        for b in book_links:
            discovered.append((b, page_url))
        page_url = next_url

    seen: set[str] = set()
    unique_books: list[tuple[str, str]] = []
    for b, src in discovered:
        if b not in seen:
            seen.add(b)
            unique_books.append((b, src))
    return unique_books, True


def run() -> int:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    unique_books, ok = discover_books()
    if not ok:
        return 1

    records: list[dict] = []
    last_was_network = False
    for book_url, source_page in unique_books:
        if last_was_network:
            time.sleep(DELAY_SECONDS)
        try:
            html, source = get_page(book_url)
        except (requests.RequestException, RuntimeError) as exc:
            print(f"FETCH FAILED: {exc}", file=sys.stderr)
            return 1
        last_was_network = source == "FETCH"
        records.append(parse_book_page(html, book_url, source_page))

    RAW_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    RAW_OUTPUT.write_text(
        json.dumps(records, indent=2, ensure_ascii=False)
    )

    print(json.dumps(records[0], indent=2, ensure_ascii=False))
    print(f"detail_pages={len(records)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
