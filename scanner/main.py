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

    logger.info(f"Starting scan across {len(brokerages)} brokerages")

    for brokerage in brokerages:
        logger.info(f"Scanning {brokerage['name']} ...")
        try:
            for listing in fetch_listings(brokerage, defaults):
                total_listings += 1
                url = listing["url"]

                if is_seen(url):
                    logger.debug(f"Already seen: {url}")
                    continue

                listing_id = record_listing(
                    url=url,
                    brokerage=brokerage["name"],
                    title=listing.get("title"),
                )

                # Download PDF if available
                pdf_path = None
                if listing.get("pdf_url") and defaults.get("pdf_download", True):
                    pdf_path = download_pdf(listing["pdf_url"], listing_id)

                # Analyze with Claude
                result = analyze_listing(listing, criteria, pdf_path)

                if result.get("matched"):
                    total_matches += 1
                    logger.info(f"MATCH: {result.get('title')} — {result.get('market')} {result.get('asset_type')}")

                    # Update DB with extracted info
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

                    # Create GitHub Issue
                    issue_number = create_github_issue(result)
                    if issue_number:
                        update_issue_number(listing_id, issue_number)

                    # Send Teams alert
                    send_teams_alert(result, issue_number)

                    # AtlasX stub — will activate when MCP is ready
                    add_to_atlasх(result)

        except Exception as e:
            total_errors += 1
            logger.error(f"Error scanning {brokerage['name']}: {e}", exc_info=True)

    finish_run(run_id, total_listings, total_matches, total_errors)
    logger.info(
        f"Scan complete — {total_listings} listings checked, "
        f"{total_matches} matches, {total_errors} errors"
    )
    return total_matches


if __name__ == "__main__":
    matches = run_scan()
    sys.exit(0)
