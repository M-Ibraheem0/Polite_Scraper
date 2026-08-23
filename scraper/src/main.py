"""Entry point for the polite scraper.

Full pipeline: discover the first 3 catalogue pages, fetch and parse
each book detail page, normalise and validate every record, and write
the good records to output/books.json and bad ones to output/errors.json.

Stage 5 behaviour:
- Each book page is handled separately: one broken page is logged,
  counted, and skipped; the other 59 survive.
- A request that times out or returns a 5xx is retried once (after a
  short wait). 404 and 403 are never retried.
- Every run ends by writing output/run-report.json with honest numbers.
- `--inject-fake <URL>` appends a made-up book URL for failure testing
  (break things on our side, never by hammering the real site).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field, ValidationError

USER_AGENT = (
    "FlyRankInternshipA9/1.0 "
    "(+https://github.com/M-Ibraheem0/Polite_Scraper)"
)
TIMEOUT_SECONDS = 10
DELAY_SECONDS = 0.5
MAX_PAGES = 3
CATALOGUE_START = "https://books.toscrape.com/catalogue/page-1.html"

# Retry policy: network errors and transient statuses get one retry.
# 404 (does not exist) and 403 (site said no) are NOT retried.
MAX_ATTEMPTS = 2
RETRY_DELAY_SECONDS = 1.0
RETRYABLE_STATUS = {408, 425, 429} | set(range(500, 600))

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
RAW_OUTPUT = Path(__file__).resolve().parent.parent / "data" / "raw" / "books.json"
GOOD_OUTPUT = Path(__file__).resolve().parent.parent / "output" / "books.json"
ERROR_OUTPUT = Path(__file__).resolve().parent.parent / "output" / "errors.json"
REPORT_OUTPUT = Path(__file__).resolve().parent.parent / "output" / "run-report.json"

PRICE_RE = re.compile(r"£\s*([0-9]+(?:\.[0-9]+)?)")
RatingWord = Literal["One", "Two", "Three", "Four", "Five"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------- Schema (Pydantic) ---------- #

class BookRecord(BaseModel):
    """The finished, validated shape of a single book record."""

    title: str = Field(..., min_length=1, description="Book title.")
    product_url: str = Field(
        ...,
        pattern=r"^https://",
        description="Canonical absolute product page URL.",
    )
    price_text: str = Field(
        ...,
        min_length=1,
        description="Raw price as it appeared on the page, e.g. '£51.77'.",
    )
    price_gbp: float = Field(
        ...,
        gt=0,
        description="Numeric price in GBP parsed from price_text.",
    )
    availability_text: str = Field(
        ...,
        min_length=1,
        description="Raw availability string, e.g. 'In stock (22 available)'.",
    )
    rating_text: RatingWord = Field(
        ...,
        description="Star-rating word as it appears in the p.star-rating class.",
    )
    description: Optional[str] = Field(
        default=None,
        description="Product description, or null if the page has none.",
    )
    source_page: str = Field(
        ...,
        pattern=r"^https://",
        description="Catalogue page URL this book was discovered on.",
    )
    fetched_at: str = Field(
        ...,
        pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$",
        description="UTC ISO 8601 timestamp of when the page was fetched.",
    )


# ---------- HTTP ---------- #

class RunStats:
    """Counters for the run report."""

    def __init__(self) -> None:
        self.pages_fetched = 0  # pages we went to the network for
        self.cache_hits = 0     # pages served from the local cache


def fetch(url: str) -> tuple[int, bytes]:
    """GET `url` with retry-once for transient failures.

    Returns (status, body). Raises requests.RequestException after a
    network failure survived both attempts, or RuntimeError after a
    retryable HTTP status survived both attempts. 404/403 are returned
    immediately and never retried.
    """
    last_error: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            response = requests.get(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            last_error = exc
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(RETRY_DELAY_SECONDS)
            continue
        if response.status_code not in RETRYABLE_STATUS:
            return response.status_code, response.content
        last_error = RuntimeError(f"retryable HTTP {response.status_code}")
        if attempt < MAX_ATTEMPTS - 1:
            time.sleep(RETRY_DELAY_SECONDS)

    assert last_error is not None
    raise last_error


def cache_path_for(url: str) -> Path:
    path = urlparse(url).path.strip("/")
    return CACHE_DIR / path.replace("/", "_")


def get_page(url: str, stats: RunStats) -> tuple[bytes, str]:
    """Return (body, 'CACHE HIT' | 'FETCH') for `url`, updating `stats`.

    Raises requests.RequestException / RuntimeError on failure so the
    caller can log and skip this single page.
    """
    path = cache_path_for(url)
    if path.exists():
        stats.cache_hits += 1
        return path.read_bytes(), "CACHE HIT"

    stats.pages_fetched += 1
    status, body = fetch(url)
    if status != 200:
        raise RuntimeError(f"HTTP {status} for {url}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return body, "FETCH"


# ---------- Parsing ---------- #

def parse_catalogue(html: bytes, page_url: str) -> tuple[list[str], str | None]:
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
        "fetched_at": utc_now_iso(),
    }


# ---------- Normalize + Validate ---------- #

def parse_price_gbp(price_text: str) -> float:
    """Turn '£51.77' into 51.77. Raises ValueError if the format is unexpected."""
    if not isinstance(price_text, str):
        raise ValueError(f"price_text must be a string, got {type(price_text).__name__}")
    match = PRICE_RE.search(price_text)
    if not match:
        raise ValueError(f"could not parse price from {price_text!r}")
    return float(match.group(1))


def normalize_and_validate(raw: dict) -> tuple[dict | None, dict | None]:
    """Return (valid_record, None) on success, (None, error) on failure."""
    price_text = raw.get("price_text")
    try:
        price_gbp = parse_price_gbp(price_text)
    except ValueError as exc:
        return None, {"record": raw, "error": f"price parse: {exc}"}

    candidate = {**raw, "price_gbp": price_gbp}
    try:
        book = BookRecord(**candidate)
    except ValidationError as exc:
        return None, {"record": raw, "error": exc.errors()}

    return book.model_dump(), None


def dedupe_by_url(records: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for r in records:
        url = r["product_url"]
        if url in seen:
            continue
        seen.add(url)
        out.append(r)
    return out


# ---------- Pipeline ---------- #

def discover_books(stats: RunStats) -> tuple[list[tuple[str, str]], int, bool]:
    """Walk the first MAX_PAGES catalogue pages.

    Returns (unique_books, pages_visited, ok). Each entry is
    (book_url, source_page). ok is False if a catalogue request failed
    (nothing can be discovered then, so the run aborts).
    """
    discovered: list[tuple[str, str]] = []
    page_url: str | None = CATALOGUE_START
    last_was_network = False
    pages_visited = 0

    for _ in range(MAX_PAGES):
        if page_url is None:
            break
        if last_was_network:
            time.sleep(DELAY_SECONDS)
        try:
            html, source = get_page(page_url, stats)
        except (requests.RequestException, RuntimeError) as exc:
            print(f"FAILED CATALOGUE PAGE: {page_url}: {exc}", file=sys.stderr)
            return [], pages_visited, False
        last_was_network = source == "FETCH"
        pages_visited += 1
        book_links, next_url = parse_catalogue(html, page_url)
        for b in book_links:
            discovered.append((b, page_url))
        page_url = next_url

    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for b, src in discovered:
        if b not in seen:
            seen.add(b)
            unique.append((b, src))
    return unique, pages_visited, True


def extract_books(
    unique_books: list[tuple[str, str]], stats: RunStats
) -> tuple[list[dict], list[str]]:
    """Fetch and parse every book page; failed pages are logged and skipped.

    Returns (records, failed_urls). One broken page never kills the run.
    """
    records: list[dict] = []
    failed_urls: list[str] = []
    last_was_network = False

    for book_url, source_page in unique_books:
        if last_was_network:
            time.sleep(DELAY_SECONDS)
        try:
            html, source = get_page(book_url, stats)
        except (requests.RequestException, RuntimeError) as exc:
            failed_urls.append(book_url)
            print(f"FAILED: {book_url}: {exc}", file=sys.stderr)
            last_was_network = True
            continue
        last_was_network = source == "FETCH"
        try:
            records.append(parse_book_page(html, book_url, source_page))
        except RuntimeError as exc:
            failed_urls.append(book_url)
            print(f"FAILED PARSE: {book_url}: {exc}", file=sys.stderr)

    return records, failed_urls


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Polite scraper for Books to Scrape.")
    parser.add_argument(
        "--inject-fake",
        metavar="URL",
        default=None,
        help="Append a made-up book URL to the discovery list for failure "
        "testing (break things on our side, not by hitting the real site).",
    )
    return parser.parse_args()


def write_report(report: dict, started_at: str, start_perf: float) -> None:
    report["duration_seconds"] = round(time.perf_counter() - start_perf, 3)
    report["started_at"] = started_at
    REPORT_OUTPUT.write_text(json.dumps(report, indent=2, ensure_ascii=False))


def run() -> int:
    args = parse_args()
    stats = RunStats()
    started_at = utc_now_iso()
    start_perf = time.perf_counter()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    RAW_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    GOOD_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    ERROR_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    REPORT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    unique_books, catalogue_pages, ok = discover_books(stats)
    if not ok:
        report = {
            "catalogue_pages": catalogue_pages,
            "pages_fetched": stats.pages_fetched,
            "cache_hits": stats.cache_hits,
            "book_urls_discovered": 0,
            "valid_records": 0,
            "invalid_records": 0,
            "failed_pages": 1,
            "failed_page_urls": ["catalogue discovery failed"],
        }
        write_report(report, started_at, start_perf)
        print("FAILED: catalogue discovery aborted the run", file=sys.stderr)
        return 1

    if args.inject_fake:
        unique_books.append((args.inject_fake, CATALOGUE_START))
        print(f"injected fake URL for failure test: {args.inject_fake}")

    raw_records, failed_pages = extract_books(unique_books, stats)
    RAW_OUTPUT.write_text(json.dumps(raw_records, indent=2, ensure_ascii=False))

    good: list[dict] = []
    errors: list[dict] = []
    for raw in raw_records:
        record, error = normalize_and_validate(raw)
        if record is not None:
            good.append(record)
        else:
            errors.append(error)  # type: ignore[arg-type]

    good = dedupe_by_url(good)
    good.sort(key=lambda r: r["product_url"])

    GOOD_OUTPUT.write_text(json.dumps(good, indent=2, ensure_ascii=False))
    ERROR_OUTPUT.write_text(json.dumps(errors, indent=2, ensure_ascii=False))

    report = {
        "catalogue_pages": catalogue_pages,
        "pages_fetched": stats.pages_fetched,
        "cache_hits": stats.cache_hits,
        "book_urls_discovered": len(unique_books),
        "valid_records": len(good),
        "invalid_records": len(errors),
        "failed_pages": len(failed_pages),
        "failed_page_urls": failed_pages,
        "injected_fake_url": args.inject_fake,
    }
    write_report(report, started_at, start_perf)

    print(f"catalogue_pages={catalogue_pages}")
    print(f"pages_fetched={stats.pages_fetched}")
    print(f"cache_hits={stats.cache_hits}")
    print(f"book_urls_discovered={len(unique_books)}")
    print(f"valid_records={len(good)}")
    print(f"invalid_records={len(errors)}")
    print(f"failed_pages={len(failed_pages)}")
    print(f"run-report: {REPORT_OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
