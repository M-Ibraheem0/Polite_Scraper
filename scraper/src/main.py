"""Entry point for the polite scraper.

Full pipeline: discover the first 3 catalogue pages, fetch and parse
each of the 60 book detail pages, normalise every record, validate
against a Pydantic schema, and write the good records to
output/books.json (sorted, deduped by product_url) and the bad ones to
output/errors.json. data/raw/books.json is the intermediate raw dump.
"""

from __future__ import annotations

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

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
RAW_OUTPUT = Path(__file__).resolve().parent.parent / "data" / "raw" / "books.json"
GOOD_OUTPUT = Path(__file__).resolve().parent.parent / "output" / "books.json"
ERROR_OUTPUT = Path(__file__).resolve().parent.parent / "output" / "errors.json"

PRICE_RE = re.compile(r"£\s*([0-9]+(?:\.[0-9]+)?)")
RATING_WORDS = ("One", "Two", "Three", "Four", "Five")
RatingWord = Literal["One", "Two", "Three", "Four", "Five"]


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

def fetch(url: str) -> tuple[int, bytes]:
    response = requests.get(
        url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS
    )
    return response.status_code, response.content


def cache_path_for(url: str) -> Path:
    path = urlparse(url).path.strip("/")
    return CACHE_DIR / path.replace("/", "_")


def get_page(url: str) -> tuple[bytes, str]:
    path = cache_path_for(url)
    if path.exists():
        return path.read_bytes(), "CACHE HIT"
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
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
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
    """Return (valid_record, None) on success, (None, error) on failure.

    The error is a dict with the raw record and a human-readable reason
    so a bad row can be inspected and re-run.
    """
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

def discover_books() -> tuple[list[tuple[str, str]], bool]:
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
    unique: list[tuple[str, str]] = []
    for b, src in discovered:
        if b not in seen:
            seen.add(b)
            unique.append((b, src))
    return unique, True


def extract_books(unique_books: list[tuple[str, str]]) -> list[dict]:
    records: list[dict] = []
    last_was_network = False
    for book_url, source_page in unique_books:
        if last_was_network:
            time.sleep(DELAY_SECONDS)
        try:
            html, source = get_page(book_url)
        except (requests.RequestException, RuntimeError) as exc:
            print(f"FETCH FAILED: {exc}", file=sys.stderr)
            return []
        last_was_network = source == "FETCH"
        records.append(parse_book_page(html, book_url, source_page))
    return records


def run() -> int:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    RAW_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    GOOD_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    ERROR_OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    unique_books, ok = discover_books()
    if not ok:
        return 1

    raw_records = extract_books(unique_books)
    RAW_OUTPUT.write_text(
        json.dumps(raw_records, indent=2, ensure_ascii=False)
    )

    good: list[dict] = []
    errors: list[dict] = []
    for raw in raw_records:
        record, error = normalize_and_validate(raw)
        if record is not None:
            good.append(record)
        else:
            errors.append(error)  # type: ignore[arg-type]

    # Idempotency: dedupe by product_url, then sort so the file is
    # byte-stable across runs (apart from the per-record fetched_at).
    good = dedupe_by_url(good)
    good.sort(key=lambda r: r["product_url"])

    GOOD_OUTPUT.write_text(
        json.dumps(good, indent=2, ensure_ascii=False)
    )
    ERROR_OUTPUT.write_text(
        json.dumps(errors, indent=2, ensure_ascii=False)
    )

    print(f"raw_records={len(raw_records)}")
    print(f"good_records={len(good)}")
    print(f"error_records={len(errors)}")
    print(f"good: {GOOD_OUTPUT}")
    print(f"errors: {ERROR_OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
