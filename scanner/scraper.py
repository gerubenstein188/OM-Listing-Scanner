import time
import logging
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from pathlib import Path
from typing import Generator

logger = logging.getLogger(__name__)

PDF_DIR = Path(__file__).parent.parent / "data" / "pdfs"

# URL path segments that strongly indicate a property listing detail page
LISTING_PATH_KEYWORDS = [
    "property", "listing", "listings", "properties", "offering",
    "for-sale", "forsale", "investment", "opportunity", "deal",
    "multifamily", "apartment", "residential", "mixed-use", "land",
    "development", "build-to-rent", "btr", "portfolio", "asset",
]

# URL path segments that indicate NON-listing pages to skip
SKIP_PATH_KEYWORDS = [
    "about", "contact", "team", "careers", "news", "blog", "press",
    "services", "solutions", "insights", "research", "events", "media",
    "subscribe", "login", "register", "privacy", "terms", "sitemap",
    "podcast", "video", "webinar", "awards", "history", "leadership",
    "project-development-services",  # Cushman service page
]

# Markets to validate against listing text
TARGET_MARKETS = [
    "indianapolis", "cincinnati", "columbus", "nashville", "raleigh",
    "durham", "charlotte", "atlanta", "tampa", "orlando", "jacksonville",
    "denver", "salt lake", "las vegas", "indy", "research triangle",
    # State abbreviations as fallback
    "in", " oh ", " tn ", " nc ", " ga ", " fl ", " co ", " ut ", " nv ",
]


def _session(user_agent: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    })
    return s


def fetch_listings(brokerage: dict, defaults: dict) -> Generator[dict, None, None]:
    """
    Yield raw listing dicts from a brokerage config entry.
    """
    url = brokerage["url"]
    delay = defaults.get("request_delay_seconds", 3)
    timeout = defaults.get("timeout_seconds", 30)
    ua = defaults.get("user_agent", "Mozilla/5.0")
    retries = defaults.get("max_retries", 2)
    base_domain = urlparse(url).netloc

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
    seen_urls = set()

    for link in soup.find_all("a", href=True):
        href = link["href"].strip()
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
        text = link.get_text(strip=True).lower()

        # Skip known non-listing paths
        if any(k in path_lower for k in SKIP_PATH_KEYWORDS):
            continue

        # Direct PDF — check if it looks like an OM
        if full_url.lower().endswith(".pdf"):
            if any(k in full_url.lower() or k in text for k in ["om", "offering", "memorandum", "brochure"]):
                yield {
                    "url": full_url,
                    "title": link.get_text(strip=True) or full_url,
                    "brokerage": brokerage["name"],
                    "pdf_url": full_url,
                }
            continue

        # Check if this looks like a listing detail or listing index page
        if _looks_like_listing_url(path_lower, text):
            listing = {
                "url": full_url,
                "title": link.get_text(strip=True) or full_url,
                "brokerage": brokerage["name"],
                "pdf_url": None,
            }
            # Try to find embedded OM PDF on the detail page
            listing["pdf_url"] = _find_om_pdf(full_url, session, timeout, delay)
            yield listing
            time.sleep(delay)


def _looks_like_listing_url(path: str, link_text: str) -> bool:
    """Return True if the URL path looks like a property listing page."""
    # Must have at least one listing keyword in path or link text
    has_listing_keyword = any(k in path for k in LISTING_PATH_KEYWORDS)
    has_text_keyword = any(k in link_text for k in [
        "apartment", "unit", "multifamily", "land", "development",
        "mixed use", "townhome", "rental", "for sale", "investment",
        "btr", "build to rent", "offering",
    ])

    # Must NOT be a short top-level path (e.g. /services, /about)
    path_depth = len([p for p in path.split("/") if p])
    is_deep_enough = path_depth >= 2

    return (has_listing_keyword or has_text_keyword) and is_deep_enough


def _find_om_pdf(listing_url: str, session: requests.Session, timeout: int, delay: float) -> str | None:
    """Fetch a listing detail page and look for an OM PDF download link."""
    try:
        time.sleep(delay)
        resp = session.get(listing_url, timeout=timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        for link in soup.find_all("a", href=True):
            href = link["href"]
            href_lower = href.lower()
            text = link.get_text(strip=True).lower()
            if href_lower.endswith(".pdf") or any(
                k in href_lower or k in text
                for k in ["offering memorandum", "om download", "download om",
                          "brochure", "download brochure", "property brochure"]
            ):
                return urljoin(listing_url, href)
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
        session = _session("Mozilla/5.0")
    try:
        resp = session.get(pdf_url, timeout=timeout, stream=True)
        resp.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        logger.info(f"Downloaded PDF: {pdf_url} → {dest}")
        return dest
    except Exception as e:
        logger.error(f"Failed to download PDF {pdf_url}: {e}")
        return None
