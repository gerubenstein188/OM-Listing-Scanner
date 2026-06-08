import os
import json
import logging
import requests
from datetime import datetime

logger = logging.getLogger(__name__)

# ── GitHub Issues ──────────────────────────────────────────────────────────────

def create_github_issue(result: dict) -> int | None:
    """Create a GitHub Issue for a matched deal. Returns issue number."""
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")  # e.g. "buckingham/om-scanner"
    if not token or not repo:
        logger.warning("GITHUB_TOKEN or GITHUB_REPOSITORY not set — skipping issue creation")
        return None

    title = _issue_title(result)
    body = _issue_body(result)
    labels = _issue_labels(result)

    resp = requests.post(
        f"https://api.github.com/repos/{repo}/issues",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        json={"title": title, "body": body, "labels": labels},
        timeout=15,
    )
    if resp.status_code == 201:
        issue = resp.json()
        logger.info(f"Created GitHub issue #{issue['number']}: {title}")
        return issue["number"]
    else:
        logger.error(f"GitHub issue creation failed: {resp.status_code} {resp.text}")
        return None


def _issue_title(r: dict) -> str:
    parts = [r.get("market") or "Unknown Market"]
    if r.get("asset_type"):
        parts.append(r["asset_type"].title())
    if r.get("subtype"):
        parts.append(f"({r['subtype']})")
    if r.get("units"):
        parts.append(f"— {r['units']} units")
    parts.append(f"| {r.get('brokerage', 'Unknown Broker')}")
    return " ".join(parts)


def _issue_body(r: dict) -> str:
    cfo = r.get("call_for_offers_date")
    cfo_line = f"**Call for Offers:** {cfo}" if cfo else "**Call for Offers:** Not specified"

    broker_lines = ""
    if r.get("broker_name"):
        broker_lines += f"**Broker:** {r['broker_name']}"
    if r.get("broker_email"):
        broker_lines += f" — {r['broker_email']}"

    match_reasons = "\n".join(f"- {m}" for m in (r.get("match_reasons") or []))
    concerns = "\n".join(f"- {c}" for c in (r.get("concerns") or []))

    return f"""## Deal Summary
{r.get("summary", "_No summary available._")}

## Key Metrics
| Field | Value |
|---|---|
| **Market** | {r.get("market") or "—"} |
| **Asset Type** | {r.get("asset_type") or "—"} |
| **Subtype** | {r.get("subtype") or "—"} |
| **Units** | {r.get("units") or "—"} |
| **Asking Price** | {r.get("asking_price") or "—"} |
| **Price / Unit** | {r.get("price_per_unit") or "—"} |
| **Land (acres)** | {r.get("land_acres") or "—"} |
| **Deal Stage** | {r.get("deal_stage") or "—"} |
| **Confidence** | {r.get("confidence") or "—"} |

{cfo_line}
{broker_lines}

## Why It Matched
{match_reasons or "_No reasons provided._"}

## Concerns / Missing Info
{concerns or "_None noted._"}

## Links
- **Listing:** {r.get("url", "—")}
- **Brokerage:** {r.get("brokerage", "—")}

---
> 🤖 Sourced by OM Scanner on {datetime.utcnow().strftime("%Y-%m-%d")}
>
> **Next step:** Sign CA → review OM → evaluate for underwriting
>
> <!-- TODO: Add to AtlasX via MCP when available -->
"""


def _issue_labels(r: dict) -> list[str]:
    labels = ["deal-alert"]
    at = (r.get("asset_type") or "").lower()
    if "multifamily" in at:
        labels.append("multifamily")
    elif "build-to-rent" in at or "btr" in at:
        labels.append("build-to-rent")
    elif "land" in at:
        labels.append("land")
    if "mixed-use" in at:
        labels.append("mixed-use")
    market = r.get("market")
    if market:
        labels.append(market.lower().replace(" ", "-").replace("/", "-"))
    return labels


# ── Microsoft Teams ────────────────────────────────────────────────────────────

def send_teams_alert(result: dict, issue_number: int | None = None) -> bool:
    """Send a Teams adaptive card via incoming webhook."""
    webhook_url = os.environ.get("TEAMS_WEBHOOK_URL")
    if not webhook_url:
        logger.warning("TEAMS_WEBHOOK_URL not set — skipping Teams alert")
        return False

    card = _teams_card(result, issue_number)
    resp = requests.post(webhook_url, json=card, timeout=15)
    if resp.status_code in (200, 202):
        logger.info(f"Teams alert sent for {result.get('title')}")
        return True
    else:
        logger.error(f"Teams alert failed: {resp.status_code} {resp.text}")
        return False


def _teams_card(r: dict, issue_number: int | None) -> dict:
    facts = []
    for label, key in [
        ("Market", "market"), ("Asset Type", "asset_type"), ("Subtype", "subtype"),
        ("Units", "units"), ("Asking Price", "asking_price"), ("Price/Unit", "price_per_unit"),
        ("Deal Stage", "deal_stage"), ("Confidence", "confidence"),
    ]:
        val = r.get(key)
        if val:
            facts.append({"name": label, "value": str(val)})

    if r.get("call_for_offers_date"):
        facts.append({"name": "Call for Offers", "value": r["call_for_offers_date"]})

    actions = []
    if r.get("url"):
        actions.append({
            "@type": "OpenUri",
            "name": "View Listing",
            "targets": [{"os": "default", "uri": r["url"]}],
        })
    if issue_number:
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        if repo:
            actions.append({
                "@type": "OpenUri",
                "name": f"GitHub Issue #{issue_number}",
                "targets": [{"os": "default", "uri": f"https://github.com/{repo}/issues/{issue_number}"}],
            })

    title = _issue_title(r)
    return {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": "0076D7",
        "summary": title,
        "sections": [
            {
                "activityTitle": f"🏗️ New Deal Alert: {title}",
                "activitySubtitle": r.get("brokerage", ""),
                "activityText": r.get("summary", ""),
                "facts": facts,
            }
        ],
        "potentialAction": actions,
    }


# ── AtlasX stub ───────────────────────────────────────────────────────────────

def add_to_atlasх(result: dict) -> bool:
    """
    TODO: Connect to AtlasX via MCP when the MCP server is available.
    Placeholder — logs the deal data that would be pushed.
    """
    logger.info(
        "AtlasX MCP not yet connected. Deal data ready for push:\n%s",
        json.dumps({
            "name": _issue_title(result),
            "market": result.get("market"),
            "asset_type": result.get("asset_type"),
            "units": result.get("units"),
            "asking_price": result.get("asking_price"),
            "source_url": result.get("url"),
            "brokerage": result.get("brokerage"),
            "stage": "Sourcing",
        }, indent=2),
    )
    return False
