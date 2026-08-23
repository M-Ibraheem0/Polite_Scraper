# Polite Scraper — Books to Scrape

A small, polite scraping pipeline. Stage 0 only: classify the target before writing any code.

## Target classification

- **Site:** [Books to Scrape](https://books.toscrape.com/) — `https://books.toscrape.com/`
- **Why this site:** Books to Scrape is a sandbox built for scraping practice. The site explicitly states "We love being scraped!", which is the permission this assignment relies on. A site like this is the only kind this scraper will touch.
- **Scope:** The first **3 catalogue pages only** (`page-1.html`, `page-2.html`, `page-3.html`). That covers 60 book detail pages out of the 1,000 on the site — a small, fixed slice, not the whole catalogue.
- **Data collected (planned, from the book detail pages):** title, price, rating, availability, product page URL, and cover image URL.
- **Why this is appropriate here:** The site is a sandbox that invites practice traffic, the volume is bounded (60 records), the data is non-personal and non-sensitive, and the work is for a learning pipeline that validates and stores every record as JSON.

## robots.txt check

Requested `https://books.toscrape.com/robots.txt` once.

**Result:** no robots file found.

The server returned an nginx welcome page rather than a `robots.txt` body. A missing robots file is not permission — it is just a missing file. Combined with the site's own "We love being scraped!" statement, the sandbox is clearly the intended target, but the absence of `robots.txt` is noted here, not relied on as authorisation.

## Pledge

> I will not reuse this code on another site without checking its rules and terms first.

## Status

- [x] Stage 0 — classify scraping target
- [x] Stage 1 — fetch and cache HTML
- [x] Stage 2 — discover three catalogue pages
- [x] Stage 3 — extract book details
- [ ] Stage 4 — normalize
- [ ] Stage 5 — store
- [ ] Stage 6 — report

## Stage 1 — fetch once, cache once

`scraper/src/main.py` downloads the first catalogue page
(`https://books.toscrape.com/catalogue/page-1.html`) using a polite
user-agent and a 10s timeout, then saves the body to
`scraper/cache/catalogue-page-1.html`. Subsequent runs read the
cached file instead of hitting the network.

Run output:

```
$ python3 scraper/src/main.py   # first run
FETCH: https://books.toscrape.com/catalogue/page-1.html
bytes: 50,469

$ python3 scraper/src/main.py   # second run
CACHE HIT: scraper/cache/catalogue-page-1.html
bytes: 50,469
```

- User-Agent: `FlyRankInternshipA9/1.0 (+https://github.com/M-Ibraheem0/Polite_Scraper)`
- Timeout: 10s
- Only HTTP 200 is treated as success; anything else prints `FETCH FAILED: HTTP <code>` and exits 1.
- The cache directory is git-ignored; the saved HTML stays on disk for local development only.

## Stage 2 — discover three catalogue pages

`scraper/src/main.py` parses each cached catalogue page with Beautiful
Soup, collects the relative `h3 > a` href inside every
`article.product_pod`, and turns each one into an absolute URL with
`urllib.parse.urljoin` (no string concatenation). It then follows the
catalogue's own `li.next > a` link — page 2, then page 3 — and stops
when there is no next link or `MAX_PAGES = 3` is reached. The 60 URLs
are deduplicated with `dict.fromkeys` (order preserved).

A 0.5s `time.sleep` is inserted only between real network fetches;
cached pages never trigger a delay.

```
$ .venv/bin/python scraper/src/main.py
FETCH:     https://books.toscrape.com/catalogue/page-1.html -> 20 books
FETCH:     https://books.toscrape.com/catalogue/page-2.html -> 20 books
FETCH:     https://books.toscrape.com/catalogue/page-3.html -> 20 books
catalogue_pages=3
discovered=60
unique_urls=60

$ .venv/bin/python scraper/src/main.py   # second run, all from cache
CACHE HIT: https://books.toscrape.com/catalogue/page-1.html -> 20 books
CACHE HIT: https://books.toscrape.com/catalogue/page-2.html -> 20 books
CACHE HIT: https://books.toscrape.com/catalogue/page-3.html -> 20 books
catalogue_pages=3
discovered=60
unique_urls=60
```

### Local environment

The project uses a venv at `scraper/.venv/` (gitignored) to keep
dependencies out of the system Python:

```bash
cd scraper
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python src/main.py
```

## Stage 3 — extract the raw records

For each of the 60 book URLs discovered in Stage 2, `scraper/src/main.py`
re-uses the same `get_page()` (user-agent, 10s timeout, status check,
0.5s delay between real fetches, cache-first) and parses the body with
Beautiful Soup.

Selectors are scoped to the **product area**, not the whole document:

| Field               | Selector (inside `div.product_main` unless noted)               |
| ------------------- | --------------------------------------------------------------- |
| `title`             | `h1`                                                            |
| `price_text`        | `p.price_color`                                                 |
| `availability_text` | `p.instock.availability`                                        |
| `rating_text`       | `p.star-rating` → class other than `star-rating` (e.g. `Three`) |
| `description`       | first `<p>` after `#product_description` (may be `null`)        |

`product_url` is the URL we just fetched, `source_page` is the
catalogue page that book was discovered on, and `fetched_at` is the
current UTC time in ISO 8601 form (`2026-08-23T19:01:53Z`). The 60
records are written to `data/raw/books.json` (gitignored).

If a book page has no description, the record's `description` is
`null` — the code never invents text. A sample record from the run:

```json
{
  "title": "A Light in the Attic",
  "product_url": "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html",
  "price_text": "£51.77",
  "availability_text": "In stock (22 available)",
  "rating_text": "Three",
  "description": "It's hard to imagine a world without A Light in the Attic...",
  "source_page": "https://books.toscrape.com/catalogue/page-1.html",
  "fetched_at": "2026-08-23T19:01:53Z"
}
detail_pages=60
```

Sanity check on the saved set (60 records, all 8 keys present):

```
rating distribution: [('One', 15), ('Five', 14), ('Three', 13), ('Four', 10), ('Two', 8)]
source pages:         [page-1.html: 20, page-2.html: 20, page-3.html: 20]
unique product_url:   60
records with null description: 0  (all 60 happened to have one; the field is null-safe)
```
