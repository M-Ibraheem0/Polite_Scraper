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
- [ ] Stage 2 — extract
- [ ] Stage 3 — normalize
- [ ] Stage 4 — validate
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
