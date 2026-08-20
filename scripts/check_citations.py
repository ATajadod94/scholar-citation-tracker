#!/usr/bin/env python3
"""
Scholar Citation Tracker — Optimized + Resilient
===============================================
Goals:
- 1 API call when no citation change (was 6)
- Daily schedule → ~30 calls/mo (free tier 100)
- Never wipes data on failure — atomic writes + persistent state/log/db for resume

State:
  data/tracker_state.json — last success, failures, monthly usage
  data/run_log.jsonl     — 500-line rolling log, easy to tail
  data/tracker.db        — sqlite, survives JSON corruption, 365 runs max
"""

import json, os, sys, smtplib, logging, time, sqlite3
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
STATE_FILE = Path(__file__).resolve().parent.parent / "data" / "tracker_state.json"
LOG_FILE = Path(__file__).resolve().parent.parent / "data" / "run_log.jsonl"
DB_FILE = Path(__file__).resolve().parent.parent / "data" / "tracker.db"
MAX_ARTICLES = 500
PAGE_SIZE = 100

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# --- resilient helpers ---
def load_state():
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "last_run_at": None,
        "last_success_at": None,
        "last_total_citations": 0,
        "consecutive_failures": 0,
        "total_runs": 0,
        "total_api_calls": 0,
        "monthly_api_calls": 0,
        "month": datetime.now(timezone.utc).strftime('%Y-%m'),
        "last_error": None,
    }

def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix('.tmp')
    with open(tmp,'w') as f:
        json.dump(state,f,indent=2)
    tmp.replace(STATE_FILE)

def append_log(entry: dict):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE,'a') as f:
        f.write(json.dumps(entry, ensure_ascii=False)+"\n")
    try:
        lines = LOG_FILE.read_text().splitlines()
        if len(lines) > 500:
            LOG_FILE.write_text("\n".join(lines[-500:])+"\n")
    except Exception:
        pass

def update_db(run_entry):
    try:
        DB_FILE.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(DB_FILE)
        con.execute("CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, status TEXT, total INTEGER, h INTEGER, i10 INTEGER, api_calls INTEGER, error TEXT)")
        con.execute("INSERT INTO runs (ts,status,total,h,i10,api_calls,error) VALUES (?,?,?,?,?,?,?)",
                    (run_entry.get('ts'), run_entry.get('status'), run_entry.get('total'), run_entry.get('h'), run_entry.get('i10'), run_entry.get('api_calls'), run_entry.get('error')))
        con.commit()
        con.execute("DELETE FROM runs WHERE id NOT IN (SELECT id FROM runs ORDER BY id DESC LIMIT 365)")
        con.commit()
        con.close()
    except Exception as e:
        log.warning("DB write failed: %s", e)

# --- SerpAPI ---
def serpapi_get(params, retries=2):
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
                err = str(data["error"])
                low = err.lower()
                if any(k in low for k in ["quota","limit","exceeded","credits","rate"]):
                    log.warning("SerpAPI quota/limit hit: %s", err)
                    summary = os.environ.get("GITHUB_STEP_SUMMARY","")
                    if summary:
                        try:
                            with open(summary,"a") as f:
                                f.write(f"## ⚠️ SerpAPI quota reached\n\n{err}\n\nWorkflow will retry next schedule. No data changed.\n")
                        except Exception:
                            pass
                    sys.exit(0)
                log.error("SerpAPI error: %s", err)
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

def fetch_scholar_profile():
    log.info("Fetching profile (1 API call) …")
    return serpapi_get({
        "engine": "google_scholar_author",
        "author_id": SCHOLAR_ID,
        "hl": "en",
        "num": str(PAGE_SIZE),
    })

def fetch_extra_articles(start_offset):
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

# --- data ---
def load_previous_data():
    if DATA_FILE.exists():
        with open(DATA_FILE,"r") as f:
            return json.load(f)
    return {"scholar_id":SCHOLAR_ID,"name":SCHOLAR_NAME,"last_checked":None,"total_citations":0,"h_index":0,"i10_index":0,"articles":[],"history":[]}

def save_data(data: dict):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = DATA_FILE.with_suffix('.tmp')
    with open(tmp,"w") as f:
        json.dump(data,f,indent=2,ensure_ascii=False)
    tmp.replace(DATA_FILE)
    log.info("Data saved to %s", DATA_FILE)

def compute_diff(old_data, profile, articles):
    cited_by = profile.get("cited_by",{})
    table = cited_by.get("table",[])
    new_total=new_h=new_i10=0
    for row in table:
        if "citations" in row: new_total=row["citations"].get("all",0)
        if "h_index" in row: new_h=row["h_index"].get("all",0)
        if "i10_index" in row: new_i10=row["i10_index"].get("all",0)
    old_total=old_data.get("total_citations",0)
    old_map={a.get("title","").strip().lower(): a for a in old_data.get("articles",[]) if a.get("title")}
    new_cits=[]
    for a in articles:
        title=a.get("title","").strip()
        key=title.lower()
        raw=a.get("cited_by")
        new_count=raw.get("value",0) if isinstance(raw,dict) else 0
        new_count=new_count if isinstance(new_count,(int,float)) and new_count is not None else 0
        old_art=old_map.get(key)
        old_count=old_art.get("citation_count",0) if old_art else 0
        if int(new_count)>int(old_count):
            new_cits.append({"title":title,"old_count":old_count,"new_count":new_count,"gained":new_count-old_count,"year":a.get("year","")})
    return {"total_citations":{"old":old_total,"new":new_total,"gained":new_total-old_total},"h_index":{"old":old_data.get("h_index",0),"new":new_h},"i10_index":{"old":old_data.get("i10_index",0),"new":new_i10},"articles_with_new_citations":new_cits,"has_changes":new_total>old_total}

def build_email_html(diff):
    total=diff["total_citations"]; arts=sorted(diff["articles_with_new_citations"], key=lambda x:x["gained"], reverse=True)
    rows="".join([f"<tr><td style='padding:10px 15px;border-bottom:1px solid #eee'>{a['title']} <span style='color:#888'>({a['year']})</span></td><td style='padding:10px 15px;text-align:center'>{a['old_count']}</td><td style='padding:10px 15px;text-align:center'>{a['new_count']}</td><td style='padding:10px 15px;text-align:center'><span style='background:#e8f5e9;color:#2e7d32;padding:2px 8px;border-radius:12px'>+{a['gained']}</span></td></tr>" for a in arts[:20]])
    return f"<!DOCTYPE html><html><body>Great news! +{total['gained']} new citations. Total {total['new']} h-index {diff['h_index']['new']} i10 {diff['i10_index']['new']}<br>{rows}<br><a href='{SCHOLAR_URL}'>Profile</a></body></html>"

def send_email(diff):
    if not SENDER_EMAIL or not SENDER_PASSWORD:
        log.warning("Email creds not configured — skipping")
        return
    total=diff["total_citations"]
    subject=f"🎉 +{total['gained']} New Citations — Now at {total['new']} Total!"
    msg=MIMEMultipart("alternative"); msg["Subject"]=subject; msg["From"]=SENDER_EMAIL; msg["To"]=RECIPIENT_EMAIL
    plain=f"Congratulations {SCHOLAR_NAME}! +{total['gained']} new citations. Total {total['new']}\n{SCHOLAR_URL}\n"
    msg.attach(MIMEText(plain,"plain")); msg.attach(MIMEText(build_email_html(diff),"html"))
    with smtplib.SMTP_SSL("smtp.gmail.com",465) as server:
        server.login(SENDER_EMAIL,SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL,RECIPIENT_EMAIL,msg.as_string())
    log.info("Email sent!")

def generate_dashboard_data(data, diff):
    out=Path(__file__).resolve().parent.parent / "docs"; out.mkdir(parents=True, exist_ok=True)
    dash={"name":data["name"],"affiliation":data.get("affiliation",""),"scholar_url":SCHOLAR_URL,"total_citations":data["total_citations"],"h_index":data["h_index"],"i10_index":data["i10_index"],"last_checked":data["last_checked"],"articles":data["articles"][:50],"history":data.get("history",[])[-90:],"latest_diff":{"gained":diff["total_citations"]["gained"],"articles_count":len(diff["articles_with_new_citations"])} if diff["has_changes"] else None}
    with open(out/"data.json","w") as f:
        json.dump(f,dash,indent=2,ensure_ascii=False)
    log.info("Dashboard written")

def main():
    log.info("="*60)
    log.info("Optimized Tracker — Starting")
    log.info("="*60)
    state=load_state()
    state['total_runs']=state.get('total_runs',0)+1
    state['last_run_at']=datetime.now(timezone.utc).isoformat()
    cur_month=datetime.now(timezone.utc).strftime('%Y-%m')
    if state.get('month')!=cur_month:
        state['month']=cur_month; state['monthly_api_calls']=0
    save_state(state)
    api_calls_used=0
    old=load_previous_data()
    log.info("Prev total %d (state total %s)", old.get("total_citations",0), state.get("last_total_citations"))

    try:
        profile=fetch_scholar_profile()
        api_calls_used+=1
    except SystemExit:
        raise
    except Exception as e:
        state["consecutive_failures"]=state.get("consecutive_failures",0)+1
        state["last_error"]=str(e)
        save_state(state)
        append_log({"ts":state["last_run_at"],"status":"failure","error":str(e),"api_calls":api_calls_used})
        update_db({"ts":state["last_run_at"],"status":"failure","total":old.get("total_citations"),"h":old.get("h_index"),"i10":old.get("i10_index"),"api_calls":api_calls_used,"error":str(e)})
        raise

    cited_by=profile.get("cited_by",{}); table=cited_by.get("table",[])
    cur_total=cur_h=cur_i10=0
    for row in table:
        if "citations" in row: cur_total=row["citations"].get("all",0)
        if "h_index" in row: cur_h=row["h_index"].get("all",0)
        if "i10_index" in row: cur_i10=row["i10_index"].get("all",0)
    log.info("Live total %d (prev %d)", cur_total, old.get("total_citations",0))

    articles=profile.get("articles",[])
    log.info("Got %d articles from profile call", len(articles))

    if cur_total==old.get("total_citations",0) and cur_total!=0:
        log.info("No total change — skipping pagination (saving %d calls)", max(0, len(old.get("articles",[]))//100))
    else:
        if len(profile.get("articles",[]))>=PAGE_SIZE:
            start=PAGE_SIZE
            while start<=MAX_ARTICLES:
                try:
                    extra=fetch_extra_articles(start)
                    api_calls_used+=1
                except Exception as e:
                    log.warning("Extra page failed at %d: %s", start, e)
                    break
                if not extra:
                    break
                articles.extend(extra)
                if len(extra)<PAGE_SIZE:
                    break
                start+=PAGE_SIZE
        log.info("Full fetch complete — %d articles total (used %d calls)", len(articles), api_calls_used)

    diff=compute_diff(old, profile, articles)
    now=datetime.now(timezone.utc).isoformat()
    new_data={"scholar_id":SCHOLAR_ID,"name":SCHOLAR_NAME,"affiliation":profile.get("author",{}).get("affiliations","UC Berkeley"),"last_checked":now,"total_citations":cur_total,"h_index":cur_h,"i10_index":cur_i10,"articles":[{"title":a.get("title",""),"citation_count":(a.get("cited_by",{}).get("value",0) if isinstance(a.get("cited_by"),dict) else 0) or 0,"year":a.get("year",""),"link":a.get("link",""),"authors":a.get("authors","")} for a in articles],"history":old.get("history",[])+[{"date":now,"total_citations":cur_total,"h_index":cur_h,"i10_index":cur_i10}]}
    new_data["history"]=new_data["history"][-365:]
    save_data(new_data)
    generate_dashboard_data(new_data, diff)

    if diff["has_changes"]:
        log.info("🎉 +%d new", diff["total_citations"]["gained"])
        try:
            send_email(diff)
        except Exception as e:
            log.warning("email failed but continuing: %s", e)
        sf=os.environ.get("GITHUB_STEP_SUMMARY","")
        if sf:
            with open(sf,"a") as f:
                f.write(f"## 🎉 +{diff['total_citations']['gained']} New Citations!\n\n- Total {cur_total}\n- h {cur_h} i10 {cur_i10}\n\nAPI calls used: {api_calls_used} (optimized from 6)\n")
    else:
        log.info("No change — %d API call(s) used", api_calls_used)
        sf=os.environ.get("GITHUB_STEP_SUMMARY","")
        if sf:
            with open(sf,"a") as f:
                f.write(f"## ✅ No change — {cur_total} citations\n\nAPI calls: {api_calls_used} (saved 5)\n")

    state["consecutive_failures"]=0
    state["last_success_at"]=state["last_run_at"]
    state["last_total_citations"]=cur_total
    state["last_error"]=None
    state["total_api_calls"]=state.get("total_api_calls",0)+api_calls_used
    state["monthly_api_calls"]=state.get("monthly_api_calls",0)+api_calls_used
    save_state(state)
    append_log({"ts":state["last_run_at"],"status":"success","total":cur_total,"h":cur_h,"i10":cur_i10,"api_calls":api_calls_used,"gained":diff["total_citations"]["gained"] if diff["has_changes"] else 0})
    update_db({"ts":state["last_run_at"],"status":"success","total":cur_total,"h":cur_h,"i10":cur_i10,"api_calls":api_calls_used,"error":None})
    log.info("Done — no repeat loop, state/log/db updated")

if __name__=="__main__":
    main()
