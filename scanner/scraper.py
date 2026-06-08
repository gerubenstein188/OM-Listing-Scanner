import time
import logging
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from pathlib import Path
from typing import Generator

logger = logging.getLogger(__name__)

PDF_DIR = Path(__file__).parent.parent / "data" / "pdfs"


def _session(user_agent: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": user_agent})
    return s


def fetch_listings(brokerage: dict, defaults: dict) -> Generator[dict, None, None]:
    """
    Yield raw listing dicts {url, title, brokerage, pdf_url} from a brokerage config entry.
    Each brokerage's listing page is fetched and links that look like OMs are extracted.
    """
    url = brokerage["url"]
    delay = defaults.get("request_delay_seconds", 3)
    timeout = defaults.get("timeout_seconds", 30)
    ua = defaults.get("user_agent", "OM-Scanner/1.0")
    retries = defaults.get("max_retries", 2)

    session = _session(ua)

    for attempt in range(retries + 1):
        try:
            resp = session.get(url, timeout=timeout)
            resp.raise_for_status()
            break
        except requests.RequestException as e:
            if attempt == retries:
                logger.error(f"Failed to fetch {brokerage['name']}: {e}")
                return
            time.sleep(delay * 2)

    soup = BeautifulSoup(resp.text, "html.parser")
    links = soup.find_all("a", href=True)

    for link in links:
        href = link["href"]
        full_url = urljoin(url, href)
        text = link.get_text(strip=True).lower()

        # Look for listing detail pages and PDF OMs
        if _looks_like_listing(full_url, text):
            listing = {
                "url": full_url,
                "title": link.get_text(strip=True),
                "brokerage": brokerage["name"],
                "pdf_url": full_url if full_url.endswith(".pdf") else None,
            }
            # If it's a detail page (not a direct PDF), try to find the OM PDF within it
            if not listing["pdf_url"]:
                listing["pdf_url"] = _find_om_pdf(full_url, session, timeout, delay)

            yield listing
            time.sleep(delay)


def _looks_like_listing(url: str, link_text: str) -> bool:
    listing_keywords = [
        "listing", "property", "offering", "opportunity", "for-sale",
        "investment", "multifamily", "apartment", "residential", "land",
        "development", "mixed-use", "build-to-rent", "btr",
    ]
    url_lower = url.lower()
    if url_lower.endswith(".pdf"):
        return any(k in url_lower for k in ["om", "offering", "memorandum", "brochure"])
    return any(k in url_lower or k in link_text for k in listing_keywords)


def _find_om_pdf(listing_url: str, session: requests.Session, timeout: int, delay: float) -> str | None:
    """Fetch a listing detail page and look for an OM PDF download link."""
    try:
        time.sleep(delay)
        resp = session.get(listing_url, timeout=timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for link in soup.find_all("a", href=True):
            href = link["href"].lower()
            text = link.get_text(strip=True).lower()
            if href.endswith(".pdf") or any(k in href or k in text for k in ["om", "offering memorandum", "brochure", "download"]):
                return urljoin(listing_url, link["href"])
    except Exception as e:
        logger.debug(f"Could not find PDF at {listing_url}: {e}")
    return None


def download_pdf(pdf_url: str, listing_id: int, session: requests.Session = None, timeout: int = 30) -> Path | None:
    """Download a PDF to the local data/pdfs directory. Returns the local path."""
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    dest = PDF_DIR / f"{listing_id}.pdf"
    if dest.exists():
        return dest

    if session is None:
        session = _session("OM-Scanner/1.0")
    try:
        resp = session.get(pdf_url, timeout=timeout, stream=True)
        resp.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        return dest
    except Exception as e:
        logger.error(f"Failed to download PDF {pdf_url}: {e}")
        return None
