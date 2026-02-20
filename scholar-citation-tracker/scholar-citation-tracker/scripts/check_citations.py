#!/usr/bin/env python3
"""
Scholar Citation Tracker
========================
Monitors a Google Scholar profile for new citations and sends
congratulatory email notifications.

This script is designed to run as a GitHub Actions scheduled workflow.
It uses SerpAPI to fetch Google Scholar data reliably, compares it
against previously stored data, and sends email notifications for
any new citations detected.
"""

import json
import os
import sys
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SCHOLAR_ID = "R_1o4RIAAAAJ"
SCHOLAR_NAME = "Negar Arabzadeh"
SCHOLAR_URL = f"https://scholar.google.com/citations?user={SCHOLAR_ID}&hl=en"
RECIPIENT_EMAIL = "ngr.arabzadeh@gmail.com"

SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "")
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD", "")

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "citations.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SerpAPI helpers
# ---------------------------------------------------------------------------

def fetch_scholar_profile() -> dict:
    """Fetch the Google Scholar author profile via SerpAPI."""
    if not SERPAPI_KEY:
        log.error("SERPAPI_KEY is not set. Cannot fetch scholar data.")
        sys.exit(1)

    url = "https://serpapi.com/search.json"
    params = {
        "engine": "google_scholar_author",
        "author_id": SCHOLAR_ID,
        "api_key": SERPAPI_KEY,
        "hl": "en",
        "num": "100",
    }

    log.info("Fetching Google Scholar profile for %s …", SCHOLAR_NAME)
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if "error" in data:
        log.error("SerpAPI error: %s", data["error"])
        sys.exit(1)

    return data


def fetch_all_articles() -> list:
    """Fetch all articles from the scholar profile, handling pagination."""
    all_articles = []
    start = 0
    page_size = 100

    while True:
        url = "https://serpapi.com/search.json"
        params = {
            "engine": "google_scholar_author",
            "author_id": SCHOLAR_ID,
            "api_key": SERPAPI_KEY,
            "hl": "en",
            "start": str(start),
            "num": str(page_size),
            "sort": "pubdate",
        }

        log.info("Fetching articles starting at offset %d …", start)
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        articles = data.get("articles", [])
        if not articles:
            break

        all_articles.extend(articles)
        start += page_size

        # Safety: stop after 500 articles
        if start > 500:
            break

    return all_articles


# ---------------------------------------------------------------------------
# Data persistence
# ---------------------------------------------------------------------------

def load_previous_data() -> dict:
    """Load previously stored citation data."""
    if DATA_FILE.exists():
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {
        "scholar_id": SCHOLAR_ID,
        "name": SCHOLAR_NAME,
        "last_checked": None,
        "total_citations": 0,
        "h_index": 0,
        "i10_index": 0,
        "articles": [],
        "history": [],
    }


def save_data(data: dict) -> None:
    """Persist citation data to JSON file."""
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    log.info("Data saved to %s", DATA_FILE)


# ---------------------------------------------------------------------------
# Diff / comparison logic
# ---------------------------------------------------------------------------

def compute_diff(old_data: dict, profile: dict, articles: list) -> dict:
    """Compare old and new citation data, returning a diff summary."""
    cited_by = profile.get("cited_by", {})
    table = cited_by.get("table", [])

    new_total = 0
    new_h = 0
    new_i10 = 0
    for row in table:
        if "citations" in row:
            new_total = row["citations"].get("all", 0)
        if "h_index" in row:
            new_h = row["h_index"].get("all", 0)
        if "i10_index" in row:
            new_i10 = row["i10_index"].get("all", 0)

    old_total = old_data.get("total_citations", 0)
    old_h = old_data.get("h_index", 0)
    old_i10 = old_data.get("i10_index", 0)

    # Build article lookup from old data
    old_articles_map = {}
    for a in old_data.get("articles", []):
        key = a.get("title", "").strip().lower()
        if key:
            old_articles_map[key] = a

    new_citations_articles = []
    for a in articles:
        title = a.get("title", "").strip()
        key = title.lower()
        new_count = a.get("cited_by", {}).get("value", 0) if isinstance(a.get("cited_by"), dict) else 0
        old_article = old_articles_map.get(key)
        old_count = 0
        if old_article:
            old_count = old_article.get("citation_count", 0)
        if new_count > old_count:
            new_citations_articles.append({
                "title": title,
                "old_count": old_count,
                "new_count": new_count,
                "gained": new_count - old_count,
                "year": a.get("year", ""),
            })

    return {
        "total_citations": {
            "old": old_total,
            "new": new_total,
            "gained": new_total - old_total,
        },
        "h_index": {"old": old_h, "new": new_h},
        "i10_index": {"old": old_i10, "new": new_i10},
        "articles_with_new_citations": new_citations_articles,
        "has_changes": new_total > old_total,
    }


# ---------------------------------------------------------------------------
# Email notification
# ---------------------------------------------------------------------------

def build_email_html(diff: dict) -> str:
    """Build a beautiful HTML email for the citation notification."""
    total = diff["total_citations"]
    articles = diff["articles_with_new_citations"]

    # Sort articles by gained citations descending
    articles_sorted = sorted(articles, key=lambda x: x["gained"], reverse=True)

    articles_rows = ""
    for a in articles_sorted[:20]:  # Top 20
        articles_rows += f"""
        <tr>
            <td style="padding: 10px 15px; border-bottom: 1px solid #eee; font-size: 14px; color: #333;">
                {a['title']}
                <span style="color: #888; font-size: 12px;">({a['year']})</span>
            </td>
            <td style="padding: 10px 15px; border-bottom: 1px solid #eee; text-align: center; font-size: 14px; color: #333;">
                {a['old_count']}
            </td>
            <td style="padding: 10px 15px; border-bottom: 1px solid #eee; text-align: center; font-size: 14px; color: #333;">
                {a['new_count']}
            </td>
            <td style="padding: 10px 15px; border-bottom: 1px solid #eee; text-align: center; font-size: 14px;">
                <span style="background: #e8f5e9; color: #2e7d32; padding: 2px 8px; border-radius: 12px; font-weight: bold;">
                    +{a['gained']}
                </span>
            </td>
        </tr>"""

    html = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"></head>
    <body style="margin: 0; padding: 0; background-color: #f5f5f5; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
        <div style="max-width: 600px; margin: 0 auto; padding: 20px;">

            <!-- Header -->
            <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); border-radius: 12px 12px 0 0; padding: 30px; text-align: center;">
                <h1 style="color: #fff; margin: 0; font-size: 24px;">🎉 New Citations Alert!</h1>
                <p style="color: rgba(255,255,255,0.9); margin: 10px 0 0 0; font-size: 16px;">
                    Congratulations, {SCHOLAR_NAME}!
                </p>
            </div>

            <!-- Main Content -->
            <div style="background: #fff; padding: 30px; border-radius: 0 0 12px 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">

                <p style="font-size: 16px; color: #333; line-height: 1.6;">
                    Great news! Your Google Scholar profile has received
                    <strong style="color: #667eea;">+{total['gained']} new citation{'s' if total['gained'] != 1 else ''}</strong>
                    since the last check.
                </p>

                <!-- Stats Cards -->
                <div style="display: flex; gap: 10px; margin: 20px 0;">
                    <div style="flex: 1; background: #f8f9ff; border-radius: 8px; padding: 15px; text-align: center;">
                        <div style="font-size: 28px; font-weight: bold; color: #667eea;">{total['new']}</div>
                        <div style="font-size: 12px; color: #888; margin-top: 4px;">Total Citations</div>
                    </div>
                    <div style="flex: 1; background: #f8f9ff; border-radius: 8px; padding: 15px; text-align: center;">
                        <div style="font-size: 28px; font-weight: bold; color: #667eea;">{diff['h_index']['new']}</div>
                        <div style="font-size: 12px; color: #888; margin-top: 4px;">h-index</div>
                    </div>
                    <div style="flex: 1; background: #f8f9ff; border-radius: 8px; padding: 15px; text-align: center;">
                        <div style="font-size: 28px; font-weight: bold; color: #667eea;">{diff['i10_index']['new']}</div>
                        <div style="font-size: 12px; color: #888; margin-top: 4px;">i10-index</div>
                    </div>
                </div>

                <!-- Articles Table -->
                {"<h3 style='color: #333; margin-top: 25px;'>Papers with New Citations</h3>" if articles_rows else ""}
                {"<table style='width: 100%; border-collapse: collapse; margin-top: 10px;'>" if articles_rows else ""}
                {"<thead><tr style='background: #f8f9ff;'>" if articles_rows else ""}
                {"<th style='padding: 10px 15px; text-align: left; font-size: 13px; color: #666; font-weight: 600;'>Paper</th>" if articles_rows else ""}
                {"<th style='padding: 10px 15px; text-align: center; font-size: 13px; color: #666; font-weight: 600;'>Before</th>" if articles_rows else ""}
                {"<th style='padding: 10px 15px; text-align: center; font-size: 13px; color: #666; font-weight: 600;'>After</th>" if articles_rows else ""}
                {"<th style='padding: 10px 15px; text-align: center; font-size: 13px; color: #666; font-weight: 600;'>New</th>" if articles_rows else ""}
                {"</tr></thead>" if articles_rows else ""}
                {"<tbody>" if articles_rows else ""}
                {articles_rows}
                {"</tbody></table>" if articles_rows else ""}

                <!-- Footer -->
                <div style="margin-top: 30px; padding-top: 20px; border-top: 1px solid #eee; text-align: center;">
                    <a href="{SCHOLAR_URL}" style="display: inline-block; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: #fff; text-decoration: none; padding: 12px 30px; border-radius: 25px; font-weight: bold; font-size: 14px;">
                        View Google Scholar Profile
                    </a>
                    <p style="font-size: 12px; color: #999; margin-top: 15px;">
                        This notification was sent by the Scholar Citation Tracker.<br>
                        Checked at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
                    </p>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return html


def send_email(diff: dict) -> None:
    """Send a congratulatory email notification about new citations."""
    if not SENDER_EMAIL or not SENDER_PASSWORD:
        log.warning("Email credentials not configured. Skipping email notification.")
        log.info("Set SENDER_EMAIL and SENDER_PASSWORD environment variables to enable.")
        return

    total = diff["total_citations"]
    subject = (
        f"🎉 +{total['gained']} New Citation{'s' if total['gained'] != 1 else ''} "
        f"— Now at {total['new']} Total!"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECIPIENT_EMAIL

    # Plain text fallback
    plain = (
        f"Congratulations, {SCHOLAR_NAME}!\n\n"
        f"Your Google Scholar profile has received +{total['gained']} new citation(s).\n"
        f"Total citations: {total['new']}\n"
        f"h-index: {diff['h_index']['new']}\n"
        f"i10-index: {diff['i10_index']['new']}\n\n"
        f"View your profile: {SCHOLAR_URL}\n"
    )
    msg.attach(MIMEText(plain, "plain"))

    # HTML version
    html = build_email_html(diff)
    msg.attach(MIMEText(html, "html"))

    try:
        log.info("Sending email to %s …", RECIPIENT_EMAIL)
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.sendmail(SENDER_EMAIL, RECIPIENT_EMAIL, msg.as_string())
        log.info("Email sent successfully!")
    except Exception as e:
        log.error("Failed to send email: %s", e)
        raise


# ---------------------------------------------------------------------------
# Dashboard data generation
# ---------------------------------------------------------------------------

def generate_dashboard_data(data: dict, diff: dict) -> None:
    """Generate JSON data for the GitHub Pages dashboard."""
    dashboard_dir = Path(__file__).resolve().parent.parent / "docs"
    dashboard_dir.mkdir(parents=True, exist_ok=True)

    dashboard_data = {
        "name": data["name"],
        "affiliation": data.get("affiliation", ""),
        "scholar_url": SCHOLAR_URL,
        "total_citations": data["total_citations"],
        "h_index": data["h_index"],
        "i10_index": data["i10_index"],
        "last_checked": data["last_checked"],
        "articles": data["articles"][:50],  # Top 50 for dashboard
        "history": data.get("history", [])[-90:],  # Last 90 data points
        "latest_diff": {
            "gained": diff["total_citations"]["gained"],
            "articles_count": len(diff["articles_with_new_citations"]),
        } if diff["has_changes"] else None,
    }

    with open(dashboard_dir / "data.json", "w") as f:
        json.dump(dashboard_data, f, indent=2, ensure_ascii=False)
    log.info("Dashboard data written to docs/data.json")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    log.info("=" * 60)
    log.info("Scholar Citation Tracker — Starting check")
    log.info("=" * 60)

    # 1. Load previous data
    old_data = load_previous_data()
    log.info("Previous total citations: %d", old_data.get("total_citations", 0))

    # 2. Fetch current data from Google Scholar
    profile = fetch_scholar_profile()
    articles = fetch_all_articles()

    # 3. Extract current metrics
    cited_by = profile.get("cited_by", {})
    table = cited_by.get("table", [])

    current_total = 0
    current_h = 0
    current_i10 = 0
    for row in table:
        if "citations" in row:
            current_total = row["citations"].get("all", 0)
        if "h_index" in row:
            current_h = row["h_index"].get("all", 0)
        if "i10_index" in row:
            current_i10 = row["i10_index"].get("all", 0)

    log.info("Current total citations: %d", current_total)
    log.info("Current h-index: %d", current_h)
    log.info("Current i10-index: %d", current_i10)

    # 4. Compute diff
    diff = compute_diff(old_data, profile, articles)

    # 5. Update stored data
    now = datetime.now(timezone.utc).isoformat()
    new_data = {
        "scholar_id": SCHOLAR_ID,
        "name": SCHOLAR_NAME,
        "affiliation": profile.get("author", {}).get("affiliations", "UC Berkeley"),
        "last_checked": now,
        "total_citations": current_total,
        "h_index": current_h,
        "i10_index": current_i10,
        "articles": [
            {
                "title": a.get("title", ""),
                "citation_count": a.get("cited_by", {}).get("value", 0) if isinstance(a.get("cited_by"), dict) else 0,
                "year": a.get("year", ""),
                "link": a.get("link", ""),
                "authors": a.get("authors", ""),
            }
            for a in articles
        ],
        "history": old_data.get("history", []) + [
            {
                "date": now,
                "total_citations": current_total,
                "h_index": current_h,
                "i10_index": current_i10,
            }
        ],
    }

    # Keep only last 365 history entries
    new_data["history"] = new_data["history"][-365:]

    save_data(new_data)

    # 6. Generate dashboard data
    generate_dashboard_data(new_data, diff)

    # 7. Send email if there are new citations
    if diff["has_changes"]:
        gained = diff["total_citations"]["gained"]
        log.info("🎉 Detected +%d new citation(s)!", gained)
        send_email(diff)

        # Write summary for GitHub Actions
        summary_file = os.environ.get("GITHUB_STEP_SUMMARY", "")
        if summary_file:
            with open(summary_file, "a") as f:
                f.write(f"## 🎉 +{gained} New Citations Detected!\n\n")
                f.write(f"- **Total Citations:** {current_total}\n")
                f.write(f"- **h-index:** {current_h}\n")
                f.write(f"- **i10-index:** {current_i10}\n\n")
                if diff["articles_with_new_citations"]:
                    f.write("### Papers with New Citations\n\n")
                    f.write("| Paper | Before | After | New |\n")
                    f.write("|-------|--------|-------|-----|\n")
                    for a in diff["articles_with_new_citations"][:10]:
                        f.write(f"| {a['title'][:60]}… | {a['old_count']} | {a['new_count']} | +{a['gained']} |\n")
    else:
        log.info("No new citations detected.")

        summary_file = os.environ.get("GITHUB_STEP_SUMMARY", "")
        if summary_file:
            with open(summary_file, "a") as f:
                f.write("## ✅ No New Citations\n\n")
                f.write(f"Total citations remain at **{current_total}**.\n")

    log.info("=" * 60)
    log.info("Check complete!")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
