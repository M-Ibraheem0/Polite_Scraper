"""Entry point for the polite scraper.

Stage 0 only: classify the target before any network code is written.
Real fetching/parsing is added in later stages.
"""

TARGET_SITE = "https://books.toscrape.com/"
CATALOGUE_PAGES = 3
BOOKS_PER_PAGE = 20
EXPECTED_BOOKS = CATALOGUE_PAGES * BOOKS_PER_PAGE  # 60


def main() -> None:
    print("Polite Scraper — Stage 0")
    print(f"Target: {TARGET_SITE}")
    print(f"Scope: first {CATALOGUE_PAGES} catalogue pages "
          f"({EXPECTED_BOOKS} book pages)")
    print("No network calls yet. See scraper/README.md for the target "
          "classification and robots.txt result.")


if __name__ == "__main__":
    main()
