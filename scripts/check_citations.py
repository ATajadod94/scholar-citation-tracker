#!/usr/bin/env python3
"""
Scholar Citation Tracker — Optimized
=====================================
Minimizes SerpAPI usage to stay within free tier (100 / month):

- Default: 1 call per run (profile + first 100 articles)
- Only paginates if profile shows >100 articles AND we need full diff
- Tiered strategy:
  1. Fetch profile (1 call). Gives total_citations/h_index/i10 + first 100 articles.
  2. If total unchanged vs stored -> exit early after dashboard regen (0 extra calls)
  3. If total changed -> fetch extra pages only until 500 or empty (max +5)

With daily schedule: worst 6 calls/day = ~180/month < 100 if change rare.
But with caching early exit: avg 1/day = ~30/month → fits free tier with headroom.

Also adds:
- Backoff & quota detection: error containing "quota" / "exceeded" / "limit"
  writes friendly summary and exits 0 (no workflow failure).
- Reuses profile articles as page 0 to avoid duplicate fetch.
- Reduced history trim, etc preserved.

Original constants kept.
"""
import json, os, sys, smtplib, logging, time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone
from pathlib import Path

import requests

SCHOLAR_ID = "R_1o4RIAAAAJ"
SCHOLAR_NAME = "Negar Arabzadeh"
SCHOLAR_URL = f"https://scholar.google.com/citations?user={SCHOLAR_ID}&hl=en"
RECIPIENT_EMAIL = "ngr.arabzadeh@gmail.com"

SERPAPI_KEY = os.environ.get("SERPAPI_KEY","")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL","")
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD","")

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "citations.json"
MAX_ARTICLES = 500
PAGE_SIZE = 100

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

def serpapi_get(params, retries=2):
    """GET SerpAPI with retry and quota detection."""
    if not SERPAPI_KEY:
        log.error("SERPAPI_KEY not set")
        sys.exit(1)
    params = {**params, "api_key": SERPAPI_KEY}
    url = "https://serpapi.com/search.json"
    last_err = None
    for attempt in range(retries+1):
        try:
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                err = data["error"]
                # quota / limit -> graceful exit
                lower = str(err).lower()
                if any(k in lower for k in ["quota","limit","exceeded","credits","rate"]):
                    log.warning("SerpAPI quota/limit hit: %s", err)
                    summary = os.environ.get("GITHUB_STEP_SUMMARY","")
                    if summary:
                        try:
                            with open(summary,"a") as f:
                                f.write(f"## ⚠️ SerpAPI quota reached\n\n{err}\n\nWorkflow will retry next schedule. No data changed.\n")
                        except: pass
                    # exit 0 so workflow doesn't show as failed; we keep old data
                    sys.exit(0)
                else:
                    log.error("SerpAPI error: %s", err)
                    # retryable if tmp?
                    if attempt < retries:
                        time.sleep(2**attempt)
                        continue
                    sys.exit(1)
            return data
        except requests.RequestException as e:
            last_err = e
            if attempt < retries:
                log.warning("Request failed attempt %d: %s — retrying", attempt+1, e)
                time.sleep(2**attempt)
                continue
            raise
    if last_err:
        raise last_err

def fetch_scholar_profile() -> dict:
    log.info("Fetching profile (1 API call) …")
    return serpapi_get({
        "engine": "google_scholar_author",
        "author_id": SCHOLAR_ID,
        "hl": "en",
        "num": str(PAGE_SIZE),
    })

def fetch_extra_articles(start_offset) -> list:
    log.info("Fetching extra page offset %d …", start_offset)
    data = serpapi_get({
        "engine": "google_scholar_author",
        "author_id": SCHOLAR_ID,
        "hl": "en",
        "start": str(start_offset),
        "num": str(PAGE_SIZE),
        "sort": "pubdate",
    })
    return data.get("articles", [])

def load_previous_data() -> dict:
    if DATA_FILE.exists():
        with open(DATA_FILE,"r") as f: return json.load(f)
    return {"scholar_id":SCHOLAR_ID,"name":SCHOLAR_NAME,"last_checked":None,"total_citations":0,"h_index":0,"i10_index":0,"articles":[],"history":[]}

def save_data(data: dict):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE,"w") as f: json.dump(f,data,indent=2,ensure_ascii=False)
    log.info("Data saved to %s", DATA_FILE)

def compute_diff(old_data, profile, articles):
    cited_by = profile.get("cited_by",{})
    table = cited_by.get("table",[])
    new_total=new_h=new_i10=0
    for row in table:
        if "citations" in row: new_total=row["citations"].get("all",0)
        if "h_index" in row: new_h=row["h_index"].get("all",0)
        if "i10_index" in row: new_i10=row["i10_index"].get("all",0)
    old_total=old_data.get("total_citations",0); old_h=old_data.get("h_index",0); old_i10=old_data.get("i10_index",0)
    old_articles_map={a.get("title","").strip().lower(): a for a in old_data.get("articles",[]) if a.get("title")}
    new_citations_articles=[]
    for a in articles:
        title=a.get("title","").strip(); key=title.lower()
        raw=a.get("cited_by"); new_count=raw.get("value",0) if isinstance(raw,dict) else 0
        new_count=new_count if isinstance(new_count,(int,float)) and new_count is not None else 0
        old_article=old_articles_map.get(key); old_count=old_article.get("citation_count",0) if old_article else 0
        if int(new_count)>int(old_count):
            new_citations_articles.append({"title":title,"old_count":old_count,"new_count":new_count,"gained":new_count-old_count,"year":a.get("year","")})
    return {"total_citations":{"old":old_total,"new":new_total,"gained":new_total-old_total},"h_index":{"old":old_h,"new":new_h},"i10_index":{"old":old_i10,"new":new_i10},"articles_with_new_citations":new_citations_articles,"has_changes":new_total>old_total}

def build_email_html(diff):
    total=diff["total_citations"]; articles=diff["articles_with_new_citations"]
    articles_sorted=sorted(articles,key=lambda x:x["gained"],reverse=True)
    rows="".join([f"""<tr><td style="padding:10px 15px;border-bottom:1px solid #eee;font-size:14px;color:#333">{a['title']} <span style="color:#888;font-size:12px">({a['year']})</span></td><td style="padding:10px 15px;border-bottom:1px solid #eee;text-align:center">{a['old_count']}</td><td style="padding:10px 15px;border-bottom:1px solid #eee;text-align:center">{a['new_count']}</td><td style="padding:10px 15px;border-bottom:1px solid #eee;text-align:center"><span style="background:#e8f5e9;color:#2e7d32;padding:2px 8px;border-radius:12px;font-weight:bold">+{a['gained']}</span></td></tr>""" for a in articles_sorted[:20]])
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head><body style="margin:0;padding:0;background:#f5f5f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif"><div style="max-width:600px;margin:0 auto;padding:20px"><div style="background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);border-radius:12px 12px 0 0;padding:30px;text-align:center"><h1 style="color:#fff;margin:0;font-size:24px">🎉 New Citations Alert!</h1><p style="color:rgba(255,255,255,0.9);margin:10px 0 0 0;font-size:16px">Congratulations, {SCHOLAR_NAME}!</p></div><div style="background:#fff;padding:30px;border-radius:0 0 12px 12px;box-shadow:0 2px 10px rgba(0,0,0,0.1)"><p style="font-size:16px;color:#333;line-height:1.6">Great news! Your profile has received <strong style="color:#667eea">+{total['gained']} new citation{'s' if total['gained']!=1 else ''}</strong> since last check.</p><div style="display:flex;gap:10px;margin:20px 0"><div style="flex:1;background:#f8f9ff;border-radius:8px;padding:15px;text-align:center"><div style="font-size:28px;font-weight:bold;color:#667eea">{total['new']}</div><div style="font-size:12px;color:#888;margin-top:4px">Total Citations</div></div><div style="flex:1;background:#f8f9ff;border-radius:8px;padding:15px;text-align:center"><div style="font-size:28px;font-weight:bold;color:#667eea">{diff['h_index']['new']}</div><div style="font-size:12px;color:#888;margin-top:4px">h-index</div></div><div style="flex:1;background:#f8f9ff;border-radius:8px;padding:15px;text-align:center"><div style="font-size:28px;font-weight:bold;color:#667eea">{diff['i10_index']['new']}</div><div style="font-size:12px;color:#888;margin-top:4px">i10-index</div></div></div>{'<h3 style="color:#333;margin-top:25px">Papers with New Citations</h3><table style="width:100%;border-collapse:collapse;margin-top:10px"><thead><tr style="background:#f8f9ff"><th style="padding:10px 15px;text-align:left;font-size:13px;color:#666;font-weight:600">Paper</th><th style="padding:10px 15px;text-align:center;font-size:13px;color:#666;font-weight:600">Before</th><th style="padding:10px 15px;text-align:center;font-size:13px;color:#666;font-weight:600">After</th><th style="padding:10px 15px;text-align:center;font-size:13px;color:#666;font-weight:600">New</th></tr></thead><tbody>'+rows+'</tbody></table>' if rows else ''}<div style="margin-top:30px;padding-top:20px;border-top:1px solid #eee;text-align:center"><a href="{SCHOLAR_URL}" style="display:inline-block;background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);color:#fff;text-decoration:none;padding:12px 30px;border-radius:25px;font-weight:bold;font-size:14px">View Google Scholar Profile</a><p style="font-size:12px;color:#999;margin-top:15px">Checked at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}<br>Optimized tracker: 1 call when no change, ≤6 when full refresh</p></div></div></div></body></html>"""

def send_email(diff):
    if not SENDER_EMAIL or not SENDER_PASSWORD:
        log.warning("Email creds not configured — skipping")
        return
    total=diff["total_citations"]
    subject=f"🎉 +{total['gained']} New Citation{'s' if total['gained']!=1 else ''} — Now at {total['new']} Total!"
    msg=MIMEMultipart("alternative"); msg["Subject"]=subject; msg["From"]=SENDER_EMAIL; msg["To"]=RECIPIENT_EMAIL
    plain=f"Congratulations {SCHOLAR_NAME}! +{total['gained']} new citations. Total {total['new']} h-index {diff['h_index']['new']} i10 {diff['i10_index']['new']}\nProfile: {SCHOLAR_URL}\n"
    msg.attach(MIMEText(plain,"plain")); msg.attach(MIMEText(build_email_html(diff),"html"))
    try:
        log.info("Sending email to %s …", RECIPIENT_EMAIL)
        with smtplib.SMTP_SSL("smtp.gmail.com",465) as server:
            server.login(SENDER_EMAIL,SENDER_PASSWORD); server.sendmail(SENDER_EMAIL,RECIPIENT_EMAIL,msg.as_string())
        log.info("Email sent!")
    except Exception as e:
        log.error("Email failed: %s", e); raise

def generate_dashboard_data(data, diff):
    out=Path(__file__).resolve().parent.parent / "docs"; out.mkdir(parents=True, exist_ok=True)
    dash={"name":data["name"],"affiliation":data.get("affiliation",""),"scholar_url":SCHOLAR_URL,"total_citations":data["total_citations"],"h_index":data["h_index"],"i10_index":data["i10_index"],"last_checked":data["last_checked"],"articles":data["articles"][:50],"history":data.get("history",[])[-90:],"latest_diff":{"gained":diff["total_citations"]["gained"],"articles_count":len(diff["articles_with_new_citations"])} if diff["has_changes"] else None}
    with open(out/"data.json","w") as f: json.dump(f,dash,indent=2,ensure_ascii=False)
    log.info("Dashboard written")

def main():
    log.info("="*60); log.info("Optimized Tracker — Starting"); log.info("="*60)
    old=load_previous_data()
    log.info("Prev total %d h %d", old.get("total_citations",0), old.get("h_index",0))

    # 1 call: profile includes stats + up to 100 articles
    profile=fetch_scholar_profile()
    cited_by=profile.get("cited_by",{}); table=cited_by.get("table",[])
    cur_total=cur_h=cur_i10=0
    for row in table:
        if "citations" in row: cur_total=row["citations"].get("all",0)
        if "h_index" in row: cur_h=row["h_index"].get("all",0)
        if "i10_index" in row: cur_i10=row["i10_index"].get("all",0)
    log.info("Live total %d (prev %d)", cur_total, old.get("total_citations",0))

    # Articles from first call
    articles=profile.get("articles",[])
    log.info("Got %d articles from profile call", len(articles))

    # Early exit optimization: if total unchanged and we already have articles, skip extra pages
    if cur_total==old.get("total_citations",0) and cur_total!=0:
        log.info("No total change — skipping pagination (saving %d calls)", max(0, len(old.get("articles",[]))//100))
        # Still rebuild diff with what we have to keep dashboard fresh
    else:
        # Need full article list if citations increased OR article count grew
        # Pagination only if profile hints more than what we have OR more than 100 total
        # Scholar author API doesn't return total article count directly, but we can continue until empty
        if len(profile.get("articles",[]))>=PAGE_SIZE:
            start=PAGE_SIZE
            while start<=MAX_ARTICLES:
                extra=fetch_extra_articles(start)
                if not extra: break
                articles.extend(extra)
                if len(extra)<PAGE_SIZE: break
                start+=PAGE_SIZE
        log.info("Full fetch complete — %d articles total (used %d calls)", len(articles), 1 + (len(articles)//PAGE_SIZE))

    diff=compute_diff(old, profile, articles)
    now=datetime.now(timezone.utc).isoformat()
    new_data={"scholar_id":SCHOLAR_ID,"name":SCHOLAR_NAME,"affiliation":profile.get("author",{}).get("affiliations","UC Berkeley"),"last_checked":now,"total_citations":cur_total,"h_index":cur_h,"i10_index":cur_i10,"articles":[{"title":a.get("title",""),"citation_count":(a.get("cited_by",{}).get("value",0) if isinstance(a.get("cited_by"),dict) else 0) or 0,"year":a.get("year",""),"link":a.get("link",""),"authors":a.get("authors","")} for a in articles],"history":old.get("history",[])+[{"date":now,"total_citations":cur_total,"h_index":cur_h,"i10_index":cur_i10}]}
    new_data["history"]=new_data["history"][-365:]
    save_data(new_data)
    generate_dashboard_data(new_data, diff)

    if diff["has_changes"]:
        log.info("🎉 +%d new", diff["total_citations"]["gained"])
        send_email(diff)
        sf=os.environ.get("GITHUB_STEP_SUMMARY","")
        if sf:
            with open(sf,"a") as f:
                f.write(f"## 🎉 +{diff['total_citations']['gained']} New Citations!\n\n- Total {cur_total}\n- h {cur_h} i10 {cur_i10}\n\nAPI calls used: {1 + (len(articles)//100 if cur_total!=old.get('total_citations',0) else 0)} (optimized from 6)\n")
    else:
        log.info("No change — 1 API call used")
        sf=os.environ.get("GITHUB_STEP_SUMMARY","")
        if sf:
            with open(sf,"a") as f:
                f.write(f"## ✅ No change — {cur_total} citations\n\nAPI calls: 1 (saved 5)\n")
    log.info("Done")

if __name__=="__main__": main()
