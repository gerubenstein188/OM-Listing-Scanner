import time
import logging
import requests
from pathlib import Path
from typing import Generator
from urllib.parse import urljoin, urlparse
import threading

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

logger = logging.getLogger(__name__)

PDF_DIR = Path(__file__).parent.parent / "data" / "pdfs"

# URL path segments that indicate NON-listing pages to skip
SKIP_PATH_KEYWORDS = [
    "about", "contact", "team", "careers", "news", "blog", "press",
    "services", "solutions", "insights", "research", "events", "media",
    "subscribe", "login", "register", "privacy", "terms", "sitemap",
    "podcast", "video", "webinar", "awards", "history", "leadership",
    "project-development-services",
]

# Words in link text that suggest a real property listing
LISTING_TEXT_HINTS = [
    "apartment", "unit", "units", "multifamily", "land", "acres",
    "development", "mixed use", "mixed-use", "townhome", "townhouse",
    "rental", "for sale", "investment", "btr", "build to rent",
    "offering", "residential", "portfolio", "community", "property",
]


def fetch_listings(brokerage: dict, defaults: dict) -> Generator[dict, None, None]:
    """
    Use a headless Playwright browser to fully render each brokerage listing
    page, then extract property listing links. Yields listing dicts.
    """
    url = brokerage["url"]
    delay = defaults.get("request_delay_seconds", 2)
    timeout_ms = defaults.get("timeout_seconds", 30) * 1000
    max_listings = defaults.get("max_listings_per_brokerage", 10)
    base_domain = urlparse(url).netloc
    seen_urls: set[str] = set()
    listings_yielded = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()

        # Load the listing index page
        try:
            page.goto(url, timeout=timeout_ms, wait_until="networkidle")
        except PWTimeout:
            logger.warning(f"{brokerage['name']}: page load timed out, trying domcontentloaded")
            try:
                page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                page.wait_for_timeout(5000)  # extra 5s for JS to render
            except Exception as e:
                logger.error(f"{brokerage['name']}: failed to load — {e}")
                browser.close()
                return
        except Exception as e:
            logger.error(f"{brokerage['name']}: failed to load — {e}")
            browser.close()
            return

        # Scroll to trigger lazy-loaded listings
        _scroll_page(page)

        # Extract all links from the rendered page
        links = page.query_selector_all("a[href]")
        for link in links:
            try:
                href = link.get_attribute("href") or ""
                text = (link.inner_text() or "").strip().lower()
            except Exception:
                continue

            if not href or href.startswith("#") or href.startswith("mailto:"):
                continue

            full_url = urljoin(url, href)
            parsed = urlparse(full_url)

            # Stay on same domain
            if parsed.netloc and parsed.netloc != base_domain:
                continue

            if full_url in seen_urls:
                continue
            seen_urls.add(full_url)

            path_lower = parsed.path.lower()

            # Skip known non-listing paths
            if any(k in path_lower for k in SKIP_PATH_KEYWORDS):
                continue

            # Direct PDF OM
            if full_url.lower().endswith(".pdf"):
                if any(k in full_url.lower() or k in text
                       for k in ["om", "offering", "memorandum", "brochure"]):
                    yield {
                        "url": full_url,
                        "title": text or full_url,
                        "brokerage": brokerage["name"],
                        "pdf_url": full_url,
                    }
                continue

            # Property detail page — needs at least 2 path segments and a listing hint
            path_depth = len([p for p in parsed.path.split("/") if p])
            has_hint = any(k in path_lower or k in text for k in LISTING_TEXT_HINTS)

            if path_depth >= 2 and has_hint:
                if listings_yielded >= max_listings:
                    logger.info(f"  Reached max listings ({max_listings}) for {brokerage['name']} — stopping")
                    break

                # Visit the detail page to extract content + look for OM PDF
                detail = _scrape_detail_page(
                    full_url, page, context, timeout_ms, delay
                )
                if detail:
                    detail["brokerage"] = brokerage["name"]
                    listings_yielded += 1
                    yield detail
                    time.sleep(delay)

        browser.close()


def _scroll_page(page, steps: int = 5):
    """Scroll down the page in steps to trigger lazy-loaded content."""
    for i in range(1, steps + 1):
        page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {i}/{steps})")
        page.wait_for_timeout(800)


def _scrape_detail_page(
    url: str, page, context, timeout_ms: int, delay: float
) -> dict | None:
    """
    Open a listing detail page in a new tab, extract the title, visible text
    summary, and any OM PDF link. Returns a listing dict or None on failure.
    """
    try:
        detail_page = context.new_page()
        detail_page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
        detail_page.wait_for_timeout(3000)

        title = detail_page.title() or url

        # Grab visible page text (first 8000 chars is plenty for Claude)
        body_text = detail_page.inner_text("body")[:8000] if detail_page.query_selector("body") else ""

        # Look for OM PDF link
        pdf_url = None
        pdf_links = detail_page.query_selector_all("a[href]")
        for link in pdf_links:
            try:
                href = (link.get_attribute("href") or "").lower()
                text = (link.inner_text() or "").lower()
                if href.endswith(".pdf") or any(
                    k in href or k in text
                    for k in ["offering memorandum", "om download", "download om",
                              "brochure", "download brochure", "property brochure",
                              "view om", "get om"]
                ):
                    pdf_url = urljoin(url, link.get_attribute("href"))
                    break
            except Exception:
                continue

        detail_page.close()

        return {
            "url": url,
            "title": title,
            "body_text": body_text,
            "pdf_url": pdf_url,
        }

    except Exception as e:
        logger.debug(f"Could not scrape detail page {url}: {e}")
        try:
            detail_page.close()
        except Exception:
            pass
        return None


def download_pdf(
    pdf_url: str,
    listing_id: int,
    timeout: int = 30,
) -> Path | None:
    """Download a PDF to data/pdfs/. Returns local path or None."""
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    dest = PDF_DIR / f"{listing_id}.pdf"
    if dest.exists():
        return dest
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
        resp = requests.get(pdf_url, headers=headers, timeout=timeout, stream=True)
        resp.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        logger.info(f"Downloaded PDF: {pdf_url} → {dest}")
        return dest
    except Exception as e:
        logger.error(f"Failed to download PDF {pdf_url}: {e}")
        return None
