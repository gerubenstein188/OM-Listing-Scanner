# OM Scanner

Automated weekly scan of commercial real estate brokerage websites for multifamily, build-to-rent, and mixed-use development opportunities matching Buckingham's investment criteria.

## How It Works

1. **GitHub Actions** runs every Monday at 7 AM ET
2. **Scraper** visits configured brokerage listing pages and finds new OMs
3. **Analyzer** sends each OM (PDF text) to Claude for structured extraction and criteria matching
4. **Notifier** creates a GitHub Issue and sends a Teams card for every match
5. **AtlasX** integration stub is ready — will activate when the MCP server is available

## Target Markets

Indianapolis · Cincinnati · Columbus · Nashville · Raleigh-Durham · Charlotte · Atlanta · Tampa · Orlando · Jacksonville · Denver · Salt Lake City · Las Vegas

## Deal Criteria

| Type | Minimum | Subtypes |
|---|---|---|
| Multifamily | 150 units | 3-story walk-up, 4-story on grade, mid-rise, mixed-use |
| Build-to-Rent | 50 units | Townhome, SFD, cottage, horizontal multifamily |
| Land | — | All stages; MF/BTR/mixed-use intended use |

## Setup

### 1. Fork / clone this repo

### 2. Add GitHub Secrets

Go to **Settings → Secrets and variables → Actions** and add:

| Secret | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Your Anthropic API key |
| `TEAMS_WEBHOOK_URL` | Incoming webhook URL from your Teams channel |

`GITHUB_TOKEN` is provided automatically by GitHub Actions.

### 3. Create GitHub Issue labels

Create these labels in your repo:
- `deal-alert`
- `multifamily`
- `build-to-rent`
- `land`
- `mixed-use`
- One label per market (e.g. `indianapolis`, `nashville`, etc.)

### 4. Run manually to test

Go to **Actions → Weekly OM Scan → Run workflow**

## Configuration

### Tuning criteria — `config/criteria.yaml`
Edit markets, minimum unit counts, asset type subtypes, and exclusions without touching code.

### Adding brokerages — `config/brokerages.yaml`
Add a new entry under `brokerages:` with the listing page URL. The scraper handles the rest.

## AtlasX Integration (Coming Soon)

When the AtlasX MCP server is available, update `scanner/notifier.py` → `add_to_atlasх()` to call the MCP and push matched deals directly into your pipeline.

## Workflow

```
Scanner finds match
    → GitHub Issue created (permanent record, searchable)
    → Teams card sent (immediate alert with deal summary)
    → Sign CA → review OM
    → Add to AtlasX → underwriting
```
