"""Scrape IBM Sterling OMS documentation for offline RAG indexing.

IBM's docs site (ibm.com/docs) blocks simple HTTP requests and uses
JavaScript rendering. This script uses Playwright (headless browser)
to crawl the documentation tree.

IMPORTANT:
- Only use this for documentation you're licensed to access
- IBM ToS may restrict automated downloading — verify with your
  IBM account team or legal
- Rate-limit requests to avoid being blocked (2s delay between pages)
- Save only for local/offline use, not redistribution

Prerequisites:
    pip install playwright beautifulsoup4
    playwright install chromium

Usage:
    python scripts/scrape-ibm-docs.py --output ~/sterling-knowledge/product-docs/
"""

from __future__ import annotations

import argparse
import asyncio
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

BASE_URL = "https://www.ibm.com/docs/en/order-management-sw/10.0"
DELAY_SECONDS = 2
MAX_PAGES = 5000


async def main(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    visited: set[str] = set()
    queue: list[str] = [BASE_URL]
    count = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        while queue and count < MAX_PAGES:
            url = queue.pop(0)
            if url in visited:
                continue
            visited.add(url)

            try:
                await page.goto(url, wait_until="networkidle", timeout=30000)
                await page.wait_for_timeout(1000)

                content = await page.content()
                soup = BeautifulSoup(content, "html.parser")

                # Extract main content
                main = (
                    soup.find("main") or soup.find("article") or soup.find("div", class_="content")
                )
                if not main:
                    main = soup.body

                if main:
                    # Save as markdown-like text
                    title = soup.title.string if soup.title else url.split("/")[-1]
                    text = main.get_text(separator="\n", strip=True)

                    # Create filename from URL path
                    path_parts = urlparse(url).path.strip("/").split("/")
                    filename = "--".join(path_parts[-3:]) + ".md"
                    filename = re.sub(r"[^\w\-.]", "_", filename)

                    out_file = output_dir / filename
                    out_file.write_text(
                        f"# {title}\n\nSource: {url}\n\n{text}",
                        encoding="utf-8",
                    )
                    count += 1

                    if count % 50 == 0:
                        print(f"  [{count}] pages saved...")

                # Find links to other doc pages
                for link in soup.find_all("a", href=True):
                    href = link["href"]
                    full_url = urljoin(url, href)
                    if (
                        full_url.startswith(BASE_URL)
                        and full_url not in visited
                        and "#" not in full_url
                    ):
                        queue.append(full_url)

            except Exception as e:
                print(f"  error: {url} — {e}")

            await asyncio.sleep(DELAY_SECONDS)

        await browser.close()

    print(f"\nDone. Saved {count} pages to {output_dir}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape IBM Sterling OMS docs")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path.home() / "sterling-knowledge" / "product-docs",
        help="Output directory",
    )
    parser.add_argument(
        "--base-url",
        default=BASE_URL,
        help="Starting URL",
    )
    args = parser.parse_args()

    print(f"Scraping Sterling OMS docs from: {args.base_url}")
    print(f"Output: {args.output}")
    print(f"Rate limit: {DELAY_SECONDS}s between pages")
    print(f"Max pages: {MAX_PAGES}")
    print()

    asyncio.run(main(args.output))
