# Scholar Citation Tracker

A production-ready, fully automated system that monitors **Negar Arabzadeh's** Google Scholar profile for new citations and sends beautifully formatted congratulatory email notifications. The project includes a live dashboard hosted on GitHub Pages and a GitHub Actions workflow that runs every 6 hours.

---

## Features

| Feature | Description |
|---|---|
| **Automated Monitoring** | GitHub Actions cron job checks for new citations every 6 hours |
| **Email Notifications** | Sends a beautifully formatted HTML email when new citations are detected |
| **Live Dashboard** | GitHub Pages site with real-time citation stats, growth chart, and publication list |
| **Citation History** | Tracks citation growth over time with interactive Chart.js visualization |
| **Publication Search** | Searchable, paginated table of all publications with citation counts |
| **Per-Paper Tracking** | Identifies exactly which papers received new citations |
| **Zero Maintenance** | Fully automated — runs indefinitely with no manual intervention |

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   GitHub Actions                     │
│                  (every 6 hours)                     │
│                                                     │
│  ┌──────────┐    ┌──────────┐    ┌──────────────┐  │
│  │ SerpAPI  │───>│  Python  │───>│ Email (SMTP) │  │
│  │  Fetch   │    │  Compare │    │ Notification │  │
│  └──────────┘    └────┬─────┘    └──────────────┘  │
│                       │                             │
│                  ┌────▼─────┐                       │
│                  │  Update  │                       │
│                  │  Data &  │                       │
│                  │ Dashboard│                       │
│                  └────┬─────┘                       │
│                       │                             │
└───────────────────────┼─────────────────────────────┘
                        │
                   ┌────▼─────┐
                   │  GitHub  │
                   │  Pages   │
                   │Dashboard │
                   └──────────┘
```

---

## Quick Start

### 1. Fork or Clone This Repository

```bash
git clone https://github.com/YOUR_USERNAME/scholar-citation-tracker.git
cd scholar-citation-tracker
```

### 2. Get a SerpAPI Key (Free)

1. Go to [serpapi.com](https://serpapi.com/) and create a free account
2. Navigate to your [API Key page](https://serpapi.com/manage-api-key)
3. Copy your API key (free tier includes 100 searches/month — more than enough for 4 checks/day)

### 3. Set Up Gmail App Password

To send emails from a Gmail account, you need an **App Password** (not your regular password):

1. Go to [Google Account Security](https://myaccount.google.com/security)
2. Enable **2-Step Verification** if not already enabled
3. Go to [App Passwords](https://myaccount.google.com/apppasswords)
4. Select **Mail** and **Other (Custom name)** → name it "Scholar Tracker"
5. Copy the 16-character app password

### 4. Configure GitHub Secrets

Go to your repository **Settings → Secrets and variables → Actions** and add these three secrets:

| Secret Name | Value | Description |
|---|---|---|
| `SERPAPI_KEY` | `your_serpapi_key` | API key from serpapi.com |
| `SENDER_EMAIL` | `your.email@gmail.com` | Gmail address to send notifications from |
| `SENDER_PASSWORD` | `xxxx xxxx xxxx xxxx` | Gmail App Password (16 characters) |

### 5. Enable GitHub Pages

1. Go to repository **Settings → Pages**
2. Under **Source**, select **GitHub Actions**
3. Save

### 6. Run the Workflow

The workflow runs automatically every 6 hours. To trigger it immediately:

1. Go to **Actions** tab
2. Select **Check Scholar Citations**
3. Click **Run workflow**

---

## How It Works

The system operates in a simple but robust pipeline that executes every 6 hours via GitHub Actions.

**Step 1 — Fetch Data:** The Python script calls the SerpAPI Google Scholar Author API to retrieve the latest citation metrics and publication data for the monitored profile.

**Step 2 — Compare:** The script loads previously stored citation data from `data/citations.json` and computes a detailed diff, identifying which papers received new citations and how many.

**Step 3 — Notify:** If new citations are detected, the script constructs a beautifully formatted HTML email showing the citation gains, updated metrics, and a table of papers with new citations. This email is sent via Gmail SMTP to the configured recipient.

**Step 4 — Update:** The script saves the new citation data to `data/citations.json` and generates an updated `docs/data.json` for the dashboard. These changes are committed and pushed automatically.

**Step 5 — Deploy:** A second job deploys the `docs/` directory to GitHub Pages, making the updated dashboard immediately available.

---

## Dashboard

The dashboard is a single-page application built with vanilla HTML, CSS, and JavaScript. It features a dark theme with a modern design and includes:

- **Real-time stats cards** showing total citations, h-index, i10-index, and publication count
- **Citation growth chart** powered by Chart.js with interactive tooltips
- **Searchable publication table** with pagination and citation badges
- **Alert banner** that highlights when new citations are detected
- **Fully responsive** design that works on desktop, tablet, and mobile

---

## Email Notification

When new citations are detected, the system sends a beautifully formatted email that includes:

- Total new citations gained
- Updated citation metrics (total, h-index, i10-index)
- A table of papers that received new citations with before/after counts
- A direct link to the Google Scholar profile

---

## Customization

### Monitor a Different Scholar

Edit `scripts/check_citations.py` and update these constants:

```python
SCHOLAR_ID = "YOUR_SCHOLAR_ID"
SCHOLAR_NAME = "Scholar Name"
RECIPIENT_EMAIL = "recipient@example.com"
```

The Scholar ID can be found in the Google Scholar profile URL: `scholar.google.com/citations?user=SCHOLAR_ID`

### Change Check Frequency

Edit `.github/workflows/check-citations.yml` and modify the cron schedule:

```yaml
schedule:
  - cron: '0 */6 * * *'  # Every 6 hours
  # - cron: '0 */12 * * *'  # Every 12 hours
  # - cron: '0 9 * * *'     # Daily at 9 AM UTC
```

### Use a Different Email Provider

The script uses Gmail SMTP by default. To use a different provider, modify the `send_email()` function in `scripts/check_citations.py`:

```python
# Example: Outlook/Hotmail
with smtplib.SMTP("smtp-mail.outlook.com", 587) as server:
    server.starttls()
    server.login(SENDER_EMAIL, SENDER_PASSWORD)
    server.sendmail(SENDER_EMAIL, RECIPIENT_EMAIL, msg.as_string())
```

---

## File Structure

```
scholar-citation-tracker/
├── .github/
│   └── workflows/
│       └── check-citations.yml    # GitHub Actions workflow
├── data/
│   └── citations.json             # Stored citation data (auto-updated)
├── docs/
│   ├── index.html                 # Dashboard (GitHub Pages)
│   └── data.json                  # Dashboard data (auto-generated)
├── scripts/
│   └── check_citations.py         # Main citation checker script
├── .gitignore
├── LICENSE
├── README.md
└── requirements.txt
```

---

## Troubleshooting

**Q: The workflow fails with "SERPAPI_KEY is not set"**
Ensure you've added the `SERPAPI_KEY` secret in your repository settings under Settings → Secrets and variables → Actions.

**Q: Emails are not being sent**
Verify that `SENDER_EMAIL` and `SENDER_PASSWORD` secrets are set correctly. The password must be a Gmail App Password, not your regular Gmail password. Also ensure 2-Step Verification is enabled on the Gmail account.

**Q: The dashboard shows "No data available yet"**
The dashboard populates after the first successful workflow run. Trigger the workflow manually from the Actions tab.

**Q: SerpAPI returns an error**
Check your SerpAPI key is valid and you haven't exceeded the free tier limit (100 searches/month). Each check uses 2-3 API calls, so 4 checks/day uses about 240-360/month. Consider upgrading to a paid plan or reducing check frequency.

**Q: GitHub Pages is not deploying**
Ensure GitHub Pages is configured to use "GitHub Actions" as the source in Settings → Pages.

---

## License

MIT License — see [LICENSE](LICENSE) for details.
