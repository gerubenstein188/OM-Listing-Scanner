import json
import logging
from pathlib import Path
import anthropic
import pymupdf  # PyMuPDF

logger = logging.getLogger(__name__)

client = anthropic.Anthropic()
MODEL = "claude-opus-4-8"

SYSTEM_PROMPT = """You are a real estate investment analyst specializing in multifamily,
build-to-rent (BTR), and mixed-use development. Your job is to extract key deal metrics
from offering memorandums (OMs) and evaluate whether they match a given investment criteria set.
Be precise and conservative — only flag as a match if the deal clearly meets the criteria.
If information is missing or ambiguous, note it rather than assuming."""


def extract_text_from_pdf(pdf_path: Path) -> str:
    """Extract text from a PDF using PyMuPDF."""
    try:
        doc = pymupdf.open(str(pdf_path))
        text = ""
        for page in doc:
            text += page.get_text()
        doc.close()
        # Truncate to ~100k chars to stay within context limits
        return text[:100_000]
    except Exception as e:
        logger.error(f"Failed to extract text from {pdf_path}: {e}")
        return ""


def analyze_listing(listing: dict, criteria: dict, pdf_path: Path | None = None) -> dict:
    """
    Send listing info (and optionally PDF text) to Claude for analysis.
    Returns a structured result dict.
    """
    pdf_text = ""
    if pdf_path and pdf_path.exists():
        pdf_text = extract_text_from_pdf(pdf_path)

    prompt = _build_prompt(listing, criteria, pdf_text)

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text
        return _parse_response(raw, listing)
    except Exception as e:
        logger.error(f"Claude API error for {listing['url']}: {e}")
        return {"matched": False, "error": str(e), "url": listing["url"]}


def _build_prompt(listing: dict, criteria: dict, pdf_text: str) -> str:
    markets = [m["name"] for m in criteria.get("markets", [])]
    mf = criteria.get("asset_types", {}).get("multifamily", {})
    btr = criteria.get("asset_types", {}).get("build_to_rent", {})
    land = criteria.get("asset_types", {}).get("land", {})

    criteria_summary = f"""
INVESTMENT CRITERIA:
- Target Markets: {', '.join(markets)}
- Multifamily: min {mf.get('min_units', 150)} units; subtypes: {', '.join(mf.get('subtypes', []))}; mixed-use included: {mf.get('include_mixed_use', True)}
- Build-to-Rent: min {btr.get('min_units', 50)} units; subtypes: {', '.join(btr.get('subtypes', []))}
- Land: all stages ({', '.join(land.get('stages', []))}); intended use must be multifamily, BTR, or mixed-use
- Exclusions: senior housing, student housing, affordable/LIHTC only, hospitality, office, industrial, standalone retail
"""

    listing_info = f"""
LISTING:
- Brokerage: {listing.get('brokerage', 'Unknown')}
- Title: {listing.get('title', 'Unknown')}
- URL: {listing.get('url', '')}
"""

    pdf_section = f"\nOFFERING MEMORANDUM TEXT (first 100k chars):\n{pdf_text}" if pdf_text else "\n(No PDF text available — analyze based on listing info only)"

    return f"""{criteria_summary}
{listing_info}
{pdf_section}

Analyze this listing against the criteria above. Respond ONLY with a JSON object in this exact format:
{{
  "matched": true or false,
  "confidence": "high" | "medium" | "low",
  "market": "detected market name or null",
  "asset_type": "multifamily | build-to-rent | land | mixed-use | unknown",
  "subtype": "specific subtype detected or null",
  "units": integer or null,
  "asking_price": "string with $ or null",
  "price_per_unit": "string with $ or null",
  "land_acres": number or null,
  "deal_stage": "string or null",
  "broker_name": "individual broker contact or null",
  "broker_email": "email or null",
  "call_for_offers_date": "date string or null",
  "summary": "2-3 sentence plain English summary of the deal",
  "match_reasons": ["list of reasons it matches criteria"],
  "concerns": ["list of concerns, missing info, or reasons it does not match"]
}}"""


def _parse_response(raw: str, listing: dict) -> dict:
    # Strip markdown code fences if present
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    try:
        result = json.loads(text)
        result["url"] = listing.get("url")
        result["brokerage"] = listing.get("brokerage")
        result["title"] = listing.get("title")
        return result
    except json.JSONDecodeError:
        logger.error(f"Failed to parse Claude response as JSON: {raw[:200]}")
        return {
            "matched": False,
            "error": "JSON parse failed",
            "raw_response": raw,
            "url": listing.get("url"),
            "brokerage": listing.get("brokerage"),
        }
