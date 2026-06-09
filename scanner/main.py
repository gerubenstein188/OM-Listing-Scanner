#!/usr/bin/env python3
"""
OM Scanner — main entry point.
Run manually or via GitHub Actions weekly cron.
"""
import logging
import sys
from pathlib import Path
import yaml

from database import init_db, is_seen, record_listing, update_issue_number, start_run, finish_run
from scraper import fetch_listings, download_pdf
from analyzer import analyze_listing
from notifier import create_github_issue, send_teams_alert, add_to_atlasх

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent.parent / "config"


def load_config():
    with open(CONFIG_DIR / "criteria.yaml") as f:
        criteria = yaml.safe_load(f)
    with open(CONFIG_DIR / "brokerages.yaml") as f:
        brokerage_config = yaml.safe_load(f)
    return criteria, brokerage_config


def run_scan():
    init_db()
    criteria, brokerage_config = load_config()
    defaults = brokerage_config.get("defaults", {})
    brokerages = brokerage_config.get("brokerages", [])

    run_id = start_run()
    total_listings = 0
    total_matches = 0
    total_errors = 0
    total_claude_errors = 0

    # Startup diagnostic — print exact config being used
    import subprocess, sys
    logger.info(f"Python: {sys.version}")
    try:
        pkgs = subprocess.check_output([sys.executable, "-m", "pip", "list"], text=True)
        for line in pkgs.splitlines():
            if any(k in line.lower() for k in ["playwright", "anthropic", "requests", "beautifulsoup"]):
                logger.info(f"  Package: {line}")
    except Exception:
        pass
    logger.info(f"Starting scan across {len(brokerages)} brokerages")
    for b in brokerages:
        logger.info(f"  Config URL → {b['name']}: {b['url']}")

    for brokerage in brokerages:
        brokerage_listings = 0
        logger.info(f"Scanning {brokerage['name']} — URL: {brokerage['url']}")
        try:
            for listing in fetch_listings(brokerage, defaults):
                total_listings += 1
                brokerage_listings += 1
                url = listing["url"]

                # Log every listing found so we can see what the scraper is picking up
                logger.info(f"  [{brokerage['name']}] Found listing: {listing.get('title', 'No title')[:80]} | {url[:100]}")

                if is_seen(url):
                    logger.info(f"  Already seen — skipping")
                    continue

                listing_id = record_listing(
                    url=url,
                    brokerage=brokerage["name"],
                    title=listing.get("title"),
                )

                # Download PDF if available
                pdf_path = None
                if listing.get("pdf_url") and defaults.get("pdf_download", True):
                    logger.info(f"  Downloading PDF: {listing['pdf_url'][:100]}")
                    pdf_path = download_pdf(listing["pdf_url"], listing_id)

                # Analyze with Claude
                logger.info(f"  Sending to Claude for analysis...")
                result = analyze_listing(listing, criteria, pdf_path)

                # Log Claude's verdict for every listing
                if result.get("error"):
                    total_claude_errors += 1
                    logger.error(f"  Claude error: {result['error']}")
                else:
                    logger.info(
                        f"  Claude result — matched: {result.get('matched')} | "
                        f"confidence: {result.get('confidence')} | "
                        f"market: {result.get('market')} | "
                        f"type: {result.get('asset_type')} | "
                        f"units: {result.get('units')}"
                    )

                if result.get("matched"):
                    total_matches += 1
                    logger.info(f"  *** MATCH: {result.get('title')} — {result.get('market')} {result.get('asset_type')} ***")

                    record_listing(
                        url=url,
                        brokerage=brokerage["name"],
                        title=result.get("title") or listing.get("title"),
                        matched=True,
                        market=result.get("market"),
                        asset_type=result.get("asset_type"),
                        units=result.get("units"),
                        asking_price=result.get("asking_price"),
                    )

                    issue_number = create_github_issue(result)
                    if issue_number:
                        update_issue_number(listing_id, issue_number)

                    send_teams_alert(result, issue_number)
                    add_to_atlasх(result)

            logger.info(f"  {brokerage['name']} done — {brokerage_listings} listings found")

        except Exception as e:
            total_errors += 1
            logger.error(f"Error scanning {brokerage['name']}: {e}", exc_info=True)

    finish_run(run_id, total_listings, total_matches, total_errors)
    logger.info(
        f"\n{'='*60}\n"
        f"SCAN COMPLETE\n"
        f"  Listings checked : {total_listings}\n"
        f"  Matches found    : {total_matches}\n"
        f"  Scraper errors   : {total_errors}\n"
        f"  Claude errors    : {total_claude_errors}\n"
        f"{'='*60}"
    )
    return total_matches


if __name__ == "__main__":
    matches = run_scan()
    sys.exit(0)
