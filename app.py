import os, re, json, math, logging, secrets
from datetime import datetime, date, timedelta
from collections import defaultdict
from flask import Flask, request, jsonify, send_from_directory, send_file
from flask_cors import CORS
from flask import render_template, redirect, url_for, session, flash
import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.naive_bayes import MultinomialNB
from sklearn.pipeline import Pipeline
import random
from pymongo import MongoClient
from flask_bcrypt import Bcrypt
from authlib.integrations.flask_client import OAuth
import pdfplumber
from dateutil import parser as dparser
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak, Image
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
import io
from io import BytesIO
from datetime import timezone
try:
    from dotenv import load_dotenv
    load_dotenv()
    log_msg = "✓ Environment variables loaded from .env"
except ImportError:
    log_msg = "⚠ python-dotenv not installed. Using system environment variables."
class Config:
    # Flask
    SECRET_KEY                = os.getenv("SECRET_KEY", "budgetai-dev-secret-key-change-in-production")

    # Supabase Configuration
    SUPABASE_URL              = os.getenv('SUPABASE_URL', '')
    SUPABASE_KEY              = os.getenv('SUPABASE_KEY', '')

    # MongoDB
    MONGO_URI                 = os.getenv("MONGO_URI", "")
    MONGO_DB_NAME             = os.getenv("MONGO_DB_NAME", "project0")

    # Google OAuth
    GOOGLE_CLIENT_ID          = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET      = os.getenv("GOOGLE_CLIENT_SECRET", "")

    # Groq LLM
    GROQ_API_KEY              = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL                = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

    # File uploads
    UPLOAD_FOLDER             = os.getenv("UPLOAD_FOLDER", "/tmp/budgetai_uploads")
    ALLOWED_EXTENSIONS        = {"csv", "pdf"}
    MAX_UPLOAD_SIZE           = int(os.getenv("MAX_UPLOAD_SIZE", "50000000"))

    # Server
    HOST                      = os.getenv("HOST", "0.0.0.0")
    PORT                      = int(os.getenv("PORT", "5000"))
    DEBUG                     = os.getenv("DEBUG", "False").lower() == "true"


# ═══════════════════════════════════════════════════════════════
# APP INIT
# ═══════════════════════════════════════════════════════════════
app = Flask(__name__, template_folder="templates")
app.secret_key = Config.SECRET_KEY

# ✅ Session cookie settings — required for sessions to work on HTTP (dev)
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"]   = False   # set True only in HTTPS production
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)

# Required for OAuth nonce / state cookies over HTTP (dev only)
os.environ.setdefault("AUTHLIB_INSECURE_TRANSPORT", "1")

CORS(app,
     supports_credentials=True,
     origins=["http://localhost:5000", "http://127.0.0.1:5000"])
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("BudgetAI")

os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)

# ═══════════════════════════════════════════════════════════════
# DEPLOYMENT CONFIGURATION VALIDATION
# ═══════════════════════════════════════════════════════════════
def validate_config():
    """Validate all required environment variables and services on startup"""
    errors = []
    
    # Check required env vars
    if not Config.SECRET_KEY or Config.SECRET_KEY == "budgetai-super-secret-key-change-this-in-production":
        errors.append("⚠ WARNING: SECRET_KEY is using default value! Set FLASK_SECRET_KEY in .env for production")
    
    if not Config.MONGO_URI:
        errors.append("✗ CRITICAL: MONGO_URI not set in environment variables")
    
    if not Config.GOOGLE_CLIENT_ID or not Config.GOOGLE_CLIENT_SECRET:
        errors.append("⚠ WARNING: Google OAuth credentials not set - Google login will not work")
    
    if not Config.GROQ_API_KEY:
        errors.append("⚠ WARNING: GROQ_API_KEY not set - AI features will be limited")
    
    # Log warnings and errors
    for error in errors:
        if "CRITICAL" in error:
            log.error(error)
        else:
            log.warning(error)
    
    return len([e for e in errors if "CRITICAL" in e]) == 0  # Return False if critical errors

DEPLOYMENT_OK = validate_config()

# ── Supabase ──────────────────────────────────────────────────
# DISABLED: Using MongoDB as primary database for all user data
supabase = None
SUPABASE_OK = False
log.info("ℹ Supabase disabled - using MongoDB exclusively for data storage")

# ── MongoDB ────────────────────────────────────────────────────
def init_mongodb():
    """Initialize MongoDB connection with error handling"""
    try:
        # Test connection
        mongo_client = MongoClient(Config.MONGO_URI, serverSelectionTimeoutMS=5000)
        mongo_client.admin.command('ping')
        db = mongo_client[Config.MONGO_DB_NAME]
        log.info("✓ MongoDB connected successfully")
        return mongo_client, db
    except Exception as e:
        log.error(f"✗ MongoDB connection failed: {e}")
        log.error("Application will not work without MongoDB. Please check MONGO_URI in environment variables.")
        raise

try:
    mongo_client, db = init_mongodb()
    users_collection   = db["users"]
    investments_col    = db["investments"]
    statements_col     = db["statements"]
    manual_txns_col    = db["manual_transactions"]  # NEW: for manual transaction tracking
    
    # Create indexes for better performance
    users_collection.create_index("email", unique=True)
    statements_col.create_index("user_id")
    manual_txns_col.create_index("user_id")
    investments_col.create_index("user_id")
    
    log.info("✓ MongoDB collections initialized with indexes")
except Exception as e:
        log.error(f"✗ Failed to initialize MongoDB: {e}")
        log.error("Application cannot start without MongoDB...")
        raise
# ── Bcrypt ─────────────────────────────────────────────────────
bcrypt = Bcrypt(app)

# ── Google OAuth ───────────────────────────────────────────────
# FIX: added token_endpoint_auth_method so Google OAuth handshake works correctly
oauth  = OAuth(app)
google = oauth.register(
    name                     = "google",
    client_id                = Config.GOOGLE_CLIENT_ID,
    client_secret            = Config.GOOGLE_CLIENT_SECRET,
    server_metadata_url      = "https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs            = {
        "scope"                    : "openid email profile",
        "prompt"                   : "select_account",
        "token_endpoint_auth_method": "client_secret_post",   # ← FIX
    },
)

# ── Groq LLM ───────────────────────────────────────────────────
try:
    from groq import Groq
    GROQ_CLIENT = Groq(api_key=Config.GROQ_API_KEY)
    GROQ_OK     = True
except Exception:
    GROQ_CLIENT = None
    GROQ_OK     = False

# ── PDF / file parsing & generation ───────────────────────────
# ═══════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════
# USER MANAGEMENT HELPERS (MongoDB Only)
# ═══════════════════════════════════════════════════════════════
def save_user_to_supabase(user_data):
    """Save or update user in MongoDB (Supabase disabled)"""
    return False  # Stub function - not used

def get_user_from_supabase(email):
    """Retrieve user from MongoDB - Supabase disabled"""
    return None  # Stub function - not used

def get_user_by_id_supabase(user_id):
    """Retrieve user from MongoDB - Supabase disabled"""
    return None  # Stub function - not used

def delete_user_from_supabase(email):
    """Delete user from MongoDB - Supabase disabled"""
    return False  # Stub function - not used


# ═══════════════════════════════════════════════════════════════
# ML CATEGORISER  (TF-IDF + Naive Bayes)
# ═══════════════════════════════════════════════════════════════
TRAINING_DATA = [
    # Food & Dining
    ("zomato","Food & Dining"),("swiggy","Food & Dining"),("restaurant","Food & Dining"),
    ("cafe","Food & Dining"),("dominos","Food & Dining"),("mcdonalds","Food & Dining"),
    ("kfc","Food & Dining"),("pizza","Food & Dining"),("hotel food","Food & Dining"),
    ("food delivery","Food & Dining"),("burger","Food & Dining"),("dining","Food & Dining"),
    ("chai","Food & Dining"),("biryani","Food & Dining"),("mess","Food & Dining"),
    # Groceries
    ("bigbasket","Groceries"),("grofers","Groceries"),("blinkit","Groceries"),
    ("dmart","Groceries"),("reliance fresh","Groceries"),("nature basket","Groceries"),
    ("kirana","Groceries"),("grocery","Groceries"),("vegetables","Groceries"),
    ("milk","Groceries"),("zepto","Groceries"),("instamart","Groceries"),
    # Shopping
    ("amazon","Shopping"),("flipkart","Shopping"),("myntra","Shopping"),
    ("ajio","Shopping"),("meesho","Shopping"),("nykaa","Shopping"),
    ("shopping","Shopping"),("purchase","Shopping"),("store","Shopping"),
    ("mall","Shopping"),("snapdeal","Shopping"),("tatacliq","Shopping"),
    # Transport
    ("ola","Transport"),("uber","Transport"),("rapido","Transport"),
    ("auto","Transport"),("metro","Transport"),("irctc","Transport"),
    ("bus","Transport"),("cab","Transport"),("rickshaw","Transport"),
    ("redbus","Transport"),("ixigo","Transport"),("makemytrip cab","Transport"),
    # Fuel
    ("petrol","Fuel"),("diesel","Fuel"),("fuel","Fuel"),
    ("hp petrol","Fuel"),("bharat petroleum","Fuel"),("indian oil","Fuel"),
    ("cng","Fuel"),("iocl","Fuel"),
    # Bills & Utilities
    ("electricity","Bills & Utilities"),("airtel","Bills & Utilities"),
    ("jio","Bills & Utilities"),("bsnl","Bills & Utilities"),
    ("broadband","Bills & Utilities"),("gas","Bills & Utilities"),
    ("water bill","Bills & Utilities"),("recharge","Bills & Utilities"),
    ("bill payment","Bills & Utilities"),("utility","Bills & Utilities"),
    ("mahanagar gas","Bills & Utilities"),("indane","Bills & Utilities"),
    # Healthcare
    ("apollo","Healthcare"),("medplus","Healthcare"),("pharmacy","Healthcare"),
    ("doctor","Healthcare"),("hospital","Healthcare"),("clinic","Healthcare"),
    ("lab test","Healthcare"),("pathology","Healthcare"),("medicine","Healthcare"),
    ("healthians","Healthcare"),("netmeds","Healthcare"),("1mg","Healthcare"),
    ("gym","Healthcare"),("fitness","Healthcare"),
    # Entertainment
    ("netflix","Entertainment"),("hotstar","Entertainment"),("amazon prime","Entertainment"),
    ("spotify","Entertainment"),("youtube premium","Entertainment"),
    ("pvr","Entertainment"),("inox","Entertainment"),("cinema","Entertainment"),
    ("zee5","Entertainment"),("sonyliv","Entertainment"),("bookmyshow","Entertainment"),
    ("steam","Entertainment"),("game","Entertainment"),
    # Subscriptions
    ("subscription","Subscriptions"),("apple","Subscriptions"),
    ("microsoft","Subscriptions"),("google one","Subscriptions"),
    # Education
    ("coursera","Education"),("udemy","Education"),("byju","Education"),
    ("unacademy","Education"),("school fee","Education"),("college fee","Education"),
    ("tuition","Education"),("books","Education"),("library","Education"),
    # Travel
    ("flight","Travel"),("hotel","Travel"),("oyo","Travel"),
    ("makemytrip","Travel"),("goibibo","Travel"),("airbnb","Travel"),
    ("cleartrip","Travel"),("yatra","Travel"),("booking.com","Travel"),
    # Loan EMI
    ("emi","Loan EMI"),("loan emi","Loan EMI"),("home loan","Loan EMI"),
    ("personal loan","Loan EMI"),("car loan emi","Loan EMI"),
    ("hdfc home","Loan EMI"),("sbi loan","Loan EMI"),("icici emi","Loan EMI"),
    ("bajaj finserv emi","Loan EMI"),
    # Investment
    ("sip","Investment"),("mutual fund","Investment"),("ppf","Investment"),
    ("nps","Investment"),("groww","Investment"),("zerodha","Investment"),
    ("kuvera","Investment"),("stocks","Investment"),("fd","Investment"),
    ("rd ","Investment"),("lic","Investment"),("insurance premium","Investment"),
    # Salary / Income
    ("salary","Salary / Income"),("neft credit","Salary / Income"),
    ("payroll","Salary / Income"),("wages","Salary / Income"),("stipend","Salary / Income"),
    # Freelance
    ("freelance","Freelance"),("consultant","Freelance"),
    ("fiverr","Freelance"),("upwork","Freelance"),("client payment","Freelance"),
    # Other
    ("atm withdrawal","Other"),("atm","Other"),("cash","Other"),
    ("transfer","Other"),("neft","Other"),("imps","Other"),
]

class MLCategoriser:
    def __init__(self):
        self.pipeline = Pipeline([
            ("tfidf", TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4),
                                      max_features=8000, sublinear_tf=True)),
            ("clf",   MultinomialNB(alpha=0.3)),
        ])
        texts, labels = zip(*TRAINING_DATA)
        self.pipeline.fit(texts, labels)
        log.info("ML Categoriser trained on %d samples", len(texts))

    def predict(self, descriptions):
        clean = [d.lower().strip() for d in descriptions]
        return list(self.pipeline.predict(clean))

    def predict_one(self, desc):
        return self.predict([desc])[0]

CAT_ML = MLCategoriser()

KEYWORD_RULES = {
    "salary":"Salary / Income","payroll":"Salary / Income",
    "emi":"Loan EMI","loan emi":"Loan EMI","home loan":"Loan EMI",
    "sip":"Investment","ppf":"Investment","nps":"Investment",
    "zomato":"Food & Dining","swiggy":"Food & Dining",
    "petrol":"Fuel","diesel":"Fuel","atm":"Other",
}

def smart_categorise(desc):
    dl = desc.lower()
    for kw, cat in KEYWORD_RULES.items():
        if kw in dl:
            return cat
    return CAT_ML.predict_one(desc)


# ═══════════════════════════════════════════════════════════════
# ANOMALY DETECTION
# ═══════════════════════════════════════════════════════════════
def detect_anomalies(transactions):
    debits      = [t for t in transactions if t["type"] == "DEBIT"]
    cat_amounts = defaultdict(list)
    for t in debits:
        cat_amounts[t["category"]].append(t["amount"])

    anomalies = []
    for t in debits:
        cat  = t["category"]
        vals = cat_amounts[cat]
        if len(vals) < 2:
            continue
        mean = np.mean(vals)
        std  = np.std(vals)
        if std == 0:
            continue
        z = (t["amount"] - mean) / std
        if z > 1.5:
            ratio = round(t["amount"] / mean, 1)
            anomalies.append({
                "description": t["description"],
                "amount":      t["amount"],
                "date":        t["date"],
                "reason":      f"{ratio}× your usual {cat} spend — unusually high",
                "severity":    "high" if z > 2.5 else "medium",
            })

    for t in debits:
        if "atm" in t["description"].lower() and t["amount"] >= 2000:
            if not any(a["description"] == t["description"] for a in anomalies):
                anomalies.append({
                    "description": t["description"],
                    "amount":      t["amount"],
                    "date":        t["date"],
                    "reason":      "Large cash withdrawal — untracked spending risk",
                    "severity":    "medium",
                })
    return anomalies[:6]


# ═══════════════════════════════════════════════════════════════
# MONEY LEAK DETECTION
# ═══════════════════════════════════════════════════════════════
LEAK_RULES = [
    {
        "keywords":    ["zomato","swiggy","food delivery"],
        "title":       "Food Delivery Overspend",
        "icon":        "🍔",
        "saving_pct":  0.35,
        "advice":      "Cook at home 3x/week to cut food delivery by 35%.",
        "threshold":   1500,
    },
    {
        "keywords":    ["netflix","hotstar","amazon prime","spotify","zee5","sonyliv"],
        "title":       "OTT Subscription Pile-up",
        "icon":        "📺",
        "saving_pct":  0.50,
        "advice":      "Cancel 1-2 overlapping services — you likely don't use all of them.",
        "threshold":   500,
    },
    {
        "keywords":    ["amazon","flipkart","myntra","meesho","ajio"],
        "title":       "Impulse Online Shopping",
        "icon":        "🛍️",
        "saving_pct":  0.37,
        "advice":      "Use the 48-hour rule before buying to cut impulse purchases.",
        "threshold":   2000,
    },
    {
        "keywords":    ["ola","uber","rapido"],
        "title":       "High Cab Usage",
        "icon":        "🚕",
        "saving_pct":  0.40,
        "advice":      "Metro + auto combo can save 40% vs app-based cabs.",
        "threshold":   800,
    },
]

def detect_money_leaks(transactions):
    leaks = []
    for rule in LEAK_RULES:
        total = sum(
            t["amount"] for t in transactions
            if t["type"] == "DEBIT"
            and any(kw in t["description"].lower() for kw in rule["keywords"])
        )
        if total >= rule["threshold"]:
            saving = round(total * rule["saving_pct"])
            leaks.append({
                "title":            rule["title"],
                "icon":             rule["icon"],
                "monthly_cost":     total,
                "potential_saving": saving,
                "description":      rule["advice"],
                "severity":         "high" if total > 3000 else "medium",
            })
    return leaks


# ═══════════════════════════════════════════════════════════════
# FINANCIAL HEALTH SCORE
# ═══════════════════════════════════════════════════════════════
def compute_health_score(income, expense, emi_total, invest_total):
    if income == 0:
        return 50
    savings_rate  = (income - expense) / income
    emi_ratio     = emi_total    / income
    invest_ratio  = invest_total / income
    score = 50
    score += min(30, savings_rate  * 100)
    score -= min(25, emi_ratio     * 100)
    score += min(20, invest_ratio  * 200)
    return max(10, min(100, round(score)))


# ═══════════════════════════════════════════════════════════════
# CSV / PDF PARSERS
# ═══════════════════════════════════════════════════════════════
AMOUNT_RE = re.compile(r"[\d,]+\.\d{2}")
DATE_RE   = re.compile(
    r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}[/-]\d{2}[/-]\d{2}|"
    r"\d{1,2}\s(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s\d{4})\b",
    re.IGNORECASE,
)

def parse_date(val):
    s = str(val).strip()
    if re.match(r"^\d{1,2}[\s\-/][A-Za-z]{3}$", s):
        s = f"{s} {date.today().year}"
    elif re.match(r"^\d{1,2}[\-/]\d{1,2}$", s):
        s = f"{s}/{date.today().year}"
    try:
        return dparser.parse(s, dayfirst=True).strftime("%Y-%m-%d")
    except Exception:
        return date.today().strftime("%Y-%m-%d")

def clean_amount(val):
    if val is None:
        return 0.0
    if isinstance(val, float) and math.isnan(val):
        return 0.0
    s = str(val).strip()
    if s in ("-","–","—","","nil","n/a","N/A","NA"):
        return 0.0
    s = re.sub(r"[₹,\s]", "", s)
    try:
        return abs(float(s))
    except ValueError:
        return 0.0

def detect_columns(df):
    cols    = {c.lower().strip(): c for c in df.columns}
    mapping = {}

    for alias in ["date","txn date","transaction date","value date","posting date","trans date"]:
        if alias in cols:
            mapping["date"] = cols[alias]; break
    if "date" not in mapping:
        for k in cols:
            if "date" in k:
                mapping["date"] = cols[k]; break

    for alias in ["description","narration","particulars","details","remarks",
                  "transaction remarks","transaction details","trans description"]:
        if alias in cols:
            mapping["description"] = cols[alias]; break
    if "description" not in mapping:
        for k in cols:
            if any(w in k for w in ["desc","narr","part","detail","remark"]):
                mapping["description"] = cols[k]; break

    income_col  = next((cols[c] for c in cols if "income"  in c or "credit" in c or "deposit"    in c), None)
    expense_col = next((cols[c] for c in cols if "expense" in c or "debit"  in c or "withdrawal" in c), None)

    if income_col and expense_col:
        mapping["credit"] = income_col
        mapping["debit"]  = expense_col
    else:
        amount_col = next((cols[c] for c in cols if "amount" in c), None)
        if amount_col:
            mapping["amount"] = amount_col

    return mapping

def _payment_method(desc):
    dl = desc.lower()
    if "upi"    in dl:                                    return "UPI"
    if any(x in dl for x in ["neft","imps","rtgs"]):      return "Net Banking"
    if any(x in dl for x in ["auto","ecs","nach","si-"]): return "Auto Debit"
    if "atm"    in dl or "cash" in dl:                    return "Cash"
    if "credit card" in dl:                               return "Credit Card"
    return "Other"

def parse_csv(filepath):
    transactions = []
    try:
        df = None
        for enc in ["utf-8","latin-1","cp1252"]:
            for skip in range(0, 6):
                try:
                    candidate = pd.read_csv(filepath, encoding=enc,
                                            skiprows=skip, on_bad_lines="skip")
                    candidate.dropna(how="all", inplace=True)
                    if len(candidate.columns) >= 2 and len(candidate) >= 1:
                        mapping = detect_columns(candidate)
                        if "date" in mapping:
                            df = candidate
                            break
                except Exception:
                    continue
            if df is not None:
                break

        if df is None or df.empty:
            return []

        mapping = detect_columns(df)
        for _, row in df.iterrows():
            try:
                date_str = parse_date(row.get(mapping["date"], ""))
                desc = str(row.get(mapping.get("description",""), "Unknown")).strip()
                if not desc or desc.lower() in ("opening balance","unknown","-","nan",
                                                "closing balance","balance b/f","balance c/f"):
                    continue

                if "debit" in mapping and "credit" in mapping:
                    credit = clean_amount(row.get(mapping["credit"]))
                    debit  = clean_amount(row.get(mapping["debit"]))
                    if credit > 0:   amount, txn_type = credit, "CREDIT"
                    elif debit > 0:  amount, txn_type = debit,  "DEBIT"
                    else:            continue
                elif "amount" in mapping:
                    raw_val = row.get(mapping["amount"])
                    raw     = clean_amount(raw_val)
                    if raw == 0: continue
                    try:
                        signed   = float(str(raw_val).replace(",","").replace("₹",""))
                        txn_type = "DEBIT" if signed < 0 else "CREDIT"
                        amount   = abs(signed)
                    except Exception:
                        amount, txn_type = raw, "DEBIT"
                else:
                    continue

                transactions.append({
                    "description":    desc[:80],
                    "amount":         round(amount, 2),
                    "type":           txn_type,
                    "category":       smart_categorise(desc),
                    "date":           date_str,
                    "payment_method": _payment_method(desc),
                })
            except Exception as e:
                log.debug("Row skip: %s", e)
    except Exception as e:
        log.error("CSV parse error: %s", e)
    return transactions

def parse_pdf(filepath):
    transactions = []
    try:
        with pdfplumber.open(filepath) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                for table in tables:
                    if not table: continue
                    mapping = detect_columns(pd.DataFrame(table[1:], columns=table[0]))
                    if "date" not in mapping: continue
                    for row in table[1:]:
                        try:
                            row_dict = dict(zip(table[0], row))
                            date_str = parse_date(row_dict.get(mapping["date"],""))
                            desc     = str(row_dict.get(mapping.get("description",""),"")).strip()
                            if not desc: continue

                            if "debit" in mapping and "credit" in mapping:
                                debit  = clean_amount(row_dict.get(mapping["debit"]))
                                credit = clean_amount(row_dict.get(mapping["credit"]))
                                if debit  > 0: amount, txn_type = debit,  "DEBIT"
                                elif credit > 0: amount, txn_type = credit, "CREDIT"
                                else: continue
                            elif "amount" in mapping:
                                amount   = clean_amount(row_dict.get(mapping["amount"]))
                                txn_type = "DEBIT" if amount < 50000 else "CREDIT"
                            else:
                                continue

                            transactions.append({
                                "description":    desc[:80],
                                "amount":         round(amount, 2),
                                "type":           txn_type,
                                "category":       smart_categorise(desc),
                                "date":           date_str,
                                "payment_method": "Other",
                            })
                        except Exception:
                            continue

                if not transactions:
                    text = page.extract_text() or ""
                    for line in text.split("\n"):
                        line = line.strip()
                        if not line: continue
                        date_m  = DATE_RE.search(line)
                        amounts = AMOUNT_RE.findall(line)
                        if date_m and amounts:
                            try:
                                date_str = parse_date(date_m.group())
                                amount   = max(float(a.replace(",","")) for a in amounts)
                                desc     = DATE_RE.sub("", line)
                                for a in amounts: desc = desc.replace(a,"")
                                desc = re.sub(r"\s+", " ", desc).strip()[:80]
                                if not desc: continue
                                txn_type = "CREDIT" if any(
                                    w in desc.lower() for w in
                                    ["credit","salary","neft cr","credit by"]
                                ) else "DEBIT"
                                transactions.append({
                                    "description":    desc,
                                    "amount":         round(amount, 2),
                                    "type":           txn_type,
                                    "category":       smart_categorise(desc),
                                    "date":           date_str,
                                    "payment_method": "Other",
                                })
                            except Exception:
                                continue
    except Exception as e:
        log.error("PDF parse error: %s", e)
    return transactions


# ═══════════════════════════════════════════════════════════════
# INVESTMENT AUTO-EXTRACTOR
# ═══════════════════════════════════════════════════════════════
INV_TYPE_MAP = [
    (["sip","systematic investment","mutual fund","mf "],   "SIP"),
    (["ppf","public provident"],                            "PPF"),
    (["nps","national pension"],                            "NPS"),
    (["fd","fixed deposit","term deposit"],                 "FD"),
    (["rd","recurring deposit"],                            "RD"),
    (["lic","insurance premium","life insurance"],          "Insurance"),
    (["stocks","equity","share","nse","bse","demat"],       "Stocks"),
    (["gold","sovereign gold","sgb"],                       "Gold"),
    (["elss","tax saver fund"],                             "ELSS"),
    (["groww","zerodha","kuvera","coin","paytm money"],     "Mutual Fund"),
]

def _infer_inv_type(desc: str) -> str:
    dl = desc.lower()
    for keywords, inv_type in INV_TYPE_MAP:
        if any(kw in dl for kw in keywords):
            return inv_type
    return "Mutual Fund"

def _infer_risk(inv_type: str) -> str:
    high  = {"Stocks", "ELSS"}
    low   = {"PPF", "FD", "RD", "NPS", "Insurance", "Gold"}
    return "high" if inv_type in high else ("low" if inv_type in low else "med")

def _infer_returns(inv_type: str) -> float:
    returns_map = {
        "SIP": 12.0, "Mutual Fund": 12.0, "ELSS": 14.0, "Stocks": 15.0,
        "PPF": 7.1,  "NPS": 10.0,         "FD":   7.0,   "RD": 6.5,
        "Insurance": 6.0, "Gold": 8.0,
    }
    return returns_map.get(inv_type, 10.0)

def extract_investments_from_transactions(transactions: list) -> list:
    inv_txns = [
        t for t in transactions
        if t["type"] == "DEBIT" and t["category"] == "Investment"
    ]
    if not inv_txns:
        return []

    grouped: dict = defaultdict(lambda: {"total": 0.0, "count": 0, "last_date": ""})
    for t in inv_txns:
        key = t["description"][:40].strip()
        grouped[key]["total"]     += t["amount"]
        grouped[key]["count"]     += 1
        grouped[key]["last_date"]  = max(grouped[key]["last_date"], t.get("date",""))

    investments = []
    for i, (name, data) in enumerate(
        sorted(grouped.items(), key=lambda x: x[1]["total"], reverse=True)
    ):
        monthly_amt = round(data["total"] / max(data["count"], 1))
        inv_type    = _infer_inv_type(name)
        investments.append({
            "id":      f"auto_inv_{i}_{int(data['total'])}",
            "name":    name,
            "type":    inv_type,
            "amt":     monthly_amt,
            "ret":     _infer_returns(inv_type),
            "risk":    _infer_risk(inv_type),
            "horizon": "5 years",
            "source":  "statement",
        })

    return investments


# ═══════════════════════════════════════════════════════════════
# STATEMENT ANALYSER
# ═══════════════════════════════════════════════════════════════
CAT_COLORS = {
    "Food & Dining":"#F59E0B","Groceries":"#10B981","Shopping":"#8B5CF6",
    "Transport":"#06B6D4","Bills & Utilities":"#EF4444","Healthcare":"#EC4899",
    "Entertainment":"#F97316","Loan EMI":"#3B82F6","Investment":"#00A152",
    "Education":"#6366F1","Travel":"#14B8A6","Salary / Income":"#22C55E",
    "Freelance":"#84CC16","Fuel":"#F59E0B","Other":"#94A3B8","Subscriptions":"#8B5CF6",
}
CAT_ICONS = {
    "Food & Dining":"🍕","Groceries":"🛒","Shopping":"🛍️","Transport":"🚕",
    "Bills & Utilities":"⚡","Healthcare":"💊","Entertainment":"🎬","Loan EMI":"🏦",
    "Investment":"📈","Education":"📚","Travel":"✈️","Salary / Income":"💼",
    "Freelance":"💻","Fuel":"⛽","Other":"📌","Subscriptions":"📱",
}

def analyse_statement(transactions, filename):
    if not transactions:
        return {"error": "No transactions found"}

    income      = sum(t["amount"] for t in transactions if t["type"] == "CREDIT")
    expense     = sum(t["amount"] for t in transactions if t["type"] == "DEBIT")
    net_savings = income - expense
    savings_rate = round(net_savings / income * 100) if income > 0 else 0

    by_cat = defaultdict(float)
    for t in transactions:
        if t["type"] == "DEBIT":
            by_cat[t["category"]] += t["amount"]

    total_exp  = sum(by_cat.values()) or 1
    categories = sorted(
        [{
            "category":   cat,
            "amount":     round(amt),
            "percentage": round(amt / total_exp * 100),
            "color":      CAT_COLORS.get(cat, "#94A3B8"),
            "icon":       CAT_ICONS.get(cat, "📌"),
            "count":      sum(1 for t in transactions
                              if t["type"] == "DEBIT" and t["category"] == cat),
        } for cat, amt in by_cat.items()],
        key=lambda x: x["amount"], reverse=True,
    )

    monthly = defaultdict(lambda: {"income": 0.0, "expense": 0.0})
    for t in transactions:
        try:
            ym = t["date"][:7]
            if t["type"] == "CREDIT": monthly[ym]["income"]  += t["amount"]
            else:                      monthly[ym]["expense"] += t["amount"]
        except Exception:
            pass
    monthly_trend = {k: {"income": round(v["income"]), "expense": round(v["expense"])}
                     for k, v in sorted(monthly.items())}

    emi_total    = sum(t["amount"] for t in transactions
                       if t["type"] == "DEBIT" and t["category"] == "Loan EMI")
    invest_total = sum(t["amount"] for t in transactions
                       if t["type"] == "DEBIT" and t["category"] == "Investment")
    emi_ratio    = round(emi_total / income * 100, 1) if income > 0 else 0
    health_score = compute_health_score(income, expense, emi_total, invest_total)

    merchant_spend = defaultdict(float)
    for t in transactions:
        if t["type"] == "DEBIT":
            merchant_spend[t["description"][:30]] += t["amount"]
    top_merchants = [{"merchant": k, "total": round(v)}
                     for k, v in sorted(merchant_spend.items(),
                                        key=lambda x: x[1], reverse=True)[:10]]

    anomalies        = detect_anomalies(transactions)
    money_leaks      = detect_money_leaks(transactions)
    auto_investments = extract_investments_from_transactions(transactions)

    ai_summary = (
        f"**Your savings rate is {savings_rate}%** "
        f"{'— well above' if savings_rate > 18 else '— below'} India's average of 18%! "
        f"Top spend: {categories[0]['category'] if categories else 'N/A'} "
        f"(₹{categories[0]['amount'] if categories else 0:,}). "
        f"Health score: {health_score}/100."

    )
    ai_recommendations = [
        f"Savings rate is {savings_rate}% — {'great work!' if savings_rate > 25 else 'try to hit 25%+'}",
        f"EMI burden is {emi_ratio}% of income — {'healthy' if emi_ratio < 30 else 'consider prepaying loans'}",
        "Invest more via SIPs to build long-term wealth",
    ]

    return {
        "filename":            filename,
        "transaction_count":   len(transactions),
        "total_income":        round(income),
        "total_expense":       round(expense),
        "net_savings":         round(net_savings),
        "savings_rate":        savings_rate,
        "health_score":        health_score,
        "emi_ratio":           emi_ratio,
        "categories":          categories,
        "monthly_trend":       monthly_trend,
        "top_merchants":       top_merchants,
        "money_leaks":         money_leaks,
        "anomalies":           anomalies,
        "transactions":        transactions,
        "ai_summary":          ai_summary,
        "ai_recommendations":  ai_recommendations,
        "investments":         auto_investments,
        "invest_total":        round(invest_total),
    }


# ═══════════════════════════════════════════════════════════════
# GROQ AI ADVISOR
# ═══════════════════════════════════════════════════════════════
def ask_groq(system_prompt, history, query):
    if not GROQ_OK or not GROQ_CLIENT:
        return (
            "🤖 **AI Advisor** — Groq API key not configured. "
            f"\n\nYour question was: *{query}*"
        )
    try:
        messages = [{"role": "system", "content": system_prompt}]
        for h in history[-8:]:
            messages.append(h)
        messages.append({"role": "user", "content": query})

        resp = GROQ_CLIENT.chat.completions.create(
            model       = Config.GROQ_MODEL,
            messages    = messages,
            max_tokens  = 600,
            temperature = 0.7,
        )
        return resp.choices[0].message.content
    except Exception as e:
        log.error("Groq error: %s", e)
        return f"AI temporarily unavailable. Please try again. ({e})"


# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════
def allowed_file(filename):
    return "." in filename and \
           filename.rsplit(".", 1)[1].lower() in Config.ALLOWED_EXTENSIONS

def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to continue.", "error")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


# ═══════════════════════════════════════════════════════════════
# PDF REPORT GENERATOR
# ═══════════════════════════════════════════════════════════════
def generate_pdf_report(analysis_result, user_name, user_email):
    """Generate a comprehensive PDF report of the financial dashboard"""
    try:
        pdf_buffer = BytesIO()
        doc = SimpleDocTemplate(pdf_buffer, pagesize=A4, 
                               topMargin=0.5*inch, bottomMargin=0.5*inch,
                               leftMargin=0.5*inch, rightMargin=0.5*inch)
        
        story = []
        styles = getSampleStyleSheet()
        
        # Title and header
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=24,
            textColor=colors.HexColor('#1a4fd6'),
            spaceAfter=6,
            fontName='Helvetica-Bold'
        )
        subtitle_style = ParagraphStyle(
            'Subtitle',
            parent=styles['Normal'],
            fontSize=11,
            textColor=colors.HexColor('#4a6fa5'),
            spaceAfter=12
        )
        
        story.append(Paragraph("📊 BudgetAI Financial Dashboard Report", title_style))
        story.append(Paragraph(f"User: {user_name} ({user_email})", subtitle_style))
        story.append(Paragraph(f"Generated: {datetime.now().strftime('%d-%b-%Y %H:%M')}", subtitle_style))
        story.append(Spacer(1, 0.2*inch))
        
        # Summary Cards
        income = analysis_result.get('total_income', 0)
        expense = analysis_result.get('total_expense', 0)
        savings = analysis_result.get('net_savings', 0)
        health_score = analysis_result.get('health_score', 0)
        
        summary_data = [
            ['💰 Total Income', f'₹{income:,}'],
            ['💸 Total Expense', f'₹{expense:,}'],
            ['💚 Net Savings', f'₹{savings:,}'],
            ['❤️ Health Score', f'{health_score}/100']
        ]
        
        summary_table = Table(summary_data, colWidths=[3*inch, 2*inch])
        summary_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, 3), colors.HexColor('#eff6ff')),
            ('BACKGROUND', (1, 0), (1, 3), colors.HexColor('#dbeafe')),
            ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor('#0d1833')),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 11),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#a8c0e8'))
        ]))
        story.append(summary_table)
        story.append(Spacer(1, 0.2*inch))
        
        # Expense by Category
        story.append(Paragraph("💳 Expense Breakdown by Category", styles['Heading2']))
        
        categories = analysis_result.get('categories', [])
        if categories:
            cat_data = [['Category', 'Amount', 'Percentage']]
            for cat in categories[:10]:
                cat_data.append([
                    cat['category'],
                    f"₹{cat['amount']:,}",
                    f"{cat['percentage']}%"
                ])
            
            cat_table = Table(cat_data, colWidths=[2*inch, 1.5*inch, 1.5*inch])
            cat_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4fa3f7')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('FONTSIZE', (0, 1), (-1, -1), 9),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
                ('TOPPADDING', (0, 0), (-1, 0), 8),
                ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#bfdbfe')),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f0f5ff')])
            ]))
            story.append(cat_table)
            story.append(Spacer(1, 0.2*inch))
        
        # Top Merchants
        story.append(Paragraph("🏪 Top Merchants", styles['Heading2']))
        merchants = analysis_result.get('top_merchants', [])[:10]
        if merchants:
            merch_data = [['Merchant', 'Total Spent']]
            for m in merchants:
                merch_data.append([m['merchant'][:40], f"₹{m['total']:,}"])
            
            merch_table = Table(merch_data, colWidths=[3.5*inch, 1.5*inch])
            merch_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#a78bfa')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('FONTSIZE', (0, 1), (-1, -1), 9),
                ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#e9d5ff')),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f5f3ff')])
            ]))
            story.append(merch_table)
            story.append(Spacer(1, 0.2*inch))
        
        # Money Leaks
        leaks = analysis_result.get('money_leaks', [])
        if leaks:
            story.append(Paragraph("⚠️ Money Leaks Detected", styles['Heading2']))
            leaks_data = [['Leak Type', 'Monthly Cost', 'Potential Saving']]
            for leak in leaks:
                leaks_data.append([
                    leak['title'],
                    f"₹{leak['monthly_cost']:,}",
                    f"₹{leak['potential_saving']:,}"
                ])
            
            leaks_table = Table(leaks_data, colWidths=[2*inch, 1.75*inch, 1.75*inch])
            leaks_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#ef4444')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('FONTSIZE', (0, 1), (-1, -1), 9),
                ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#fecaca')),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#fffdf7')])
            ]))
            story.append(leaks_table)
            story.append(Spacer(1, 0.2*inch))
        
        # AI Recommendations
        story.append(PageBreak())
        story.append(Paragraph("🤖 AI Recommendations", styles['Heading2']))
        recommendations = analysis_result.get('ai_recommendations', [])
        for i, rec in enumerate(recommendations[:5], 1):
            story.append(Paragraph(f"• {rec}", styles['Normal']))
        
        # Monthly Trend Summary
        story.append(Spacer(1, 0.15*inch))
        story.append(Paragraph("📈 Monthly Trend", styles['Heading2']))
        monthly = analysis_result.get('monthly_trend', {})
        if monthly:
            trend_data = [['Month', 'Income', 'Expense', 'Savings']]
            for month, data in sorted(monthly.items())[-6:]:
                savings = data['income'] - data['expense']
                trend_data.append([
                    month,
                    f"₹{data['income']:,}",
                    f"₹{data['expense']:,}",
                    f"₹{savings:,}"
                ])
            
            trend_table = Table(trend_data, colWidths=[1.2*inch, 1.3*inch, 1.3*inch, 1.2*inch])
            trend_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#10b981')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 9),
                ('FONTSIZE', (0, 1), (-1, -1), 8),
                ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#a7f3d0')),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#ecfdf5')])
            ]))
            story.append(trend_table)
        
        # Anomalies
        anomalies = analysis_result.get('anomalies', [])
        if anomalies:
            story.append(Spacer(1, 0.15*inch))
            story.append(Paragraph("🚨 Unusual Transactions", styles['Heading2']))
            anom_data = [['Date', 'Description', 'Amount', 'Reason']]
            for anom in anomalies[:5]:
                anom_data.append([
                    anom['date'],
                    anom['description'][:25],
                    f"₹{anom['amount']:,}",
                    anom['reason'][:40]
                ])
            
            anom_table = Table(anom_data, colWidths=[1*inch, 1.5*inch, 1.2*inch, 1.8*inch])
            anom_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f59e0b')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 9),
                ('FONTSIZE', (0, 1), (-1, -1), 8),
                ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#fcd34d')),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#fffbeb')])
            ]))
            story.append(anom_table)
        
        # Build PDF
        doc.build(story)
        pdf_buffer.seek(0)
        return pdf_buffer
    
    except Exception as e:
        log.error(f"PDF generation error: {e}")
        return None


# ═══════════════════════════════════════════════════════════════
# ─── AUTH ROUTES ────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════

@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("intro"))


@app.route("/intro", methods=["GET"])
def intro():
    """Intro landing page for BudgetAI."""
    return render_template("budgetai_nexus.html")


@app.route("/index", methods=["GET"])
def index_direct():
    """Direct access to index.html for development/testing"""
    return render_template("index.html", user_name="User", user_email="", is_new_user=True)


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        first_name = request.form.get("first_name", "").strip()
        last_name  = request.form.get("last_name", "").strip()
        email      = request.form.get("email", "").strip().lower()
        mobile     = request.form.get("mobile", "").strip()
        password   = request.form.get("password", "")
        captcha    = request.form.get("captcha")

        # ✅ CAPTCHA validation
        if str(captcha) != str(session.get("captcha_answer")):
            return jsonify({"status": "error", "message": "Captcha incorrect"}), 400

        # ✅ Email validation
        if "@" not in email:
            return jsonify({"status": "error", "message": "Invalid email"}), 400

        # ✅ Password validation
        if len(password) < 6:
            return jsonify({"status": "error", "message": "Password must be at least 6 characters"}), 400

        # ✅ Check existing user
        if users_collection.find_one({"email": email}):
            return jsonify({"status": "error", "message": "Email already exists"}), 400

        # ✅ Hash password
        hashed_pw = bcrypt.generate_password_hash(password).decode("utf-8")

        try:
            result = users_collection.insert_one({
                "first_name": first_name,
                "last_name":  last_name,
                "email":      email,
                "mobile":     mobile,
                "password":   hashed_pw,
                "auth_type":  "email",
                "created_at": datetime.now(timezone.utc)
            })
        except Exception as e:
            return jsonify({"status": "error", "message": "Signup failed"}), 500

        # ✅ Auto login after signup
        session.permanent = True
        session["user_id"] = str(result.inserted_id)
        session["user_email"] = email
        session["user_name"] = first_name or email.split("@")[0]
        log.info(f"Signup successful for {email}, session set: user_id={session.get('user_id')}, user_name={session.get('user_name')}")

        return jsonify({"status": "ok"})

    # GET request
    n1 = random.randint(10, 50)
    n2 = random.randint(1, 9)
    session["captcha_answer"] = n1 + n2

    return render_template("signup.html", captcha_text=f"{n1} + {n2} = ?")


@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        # ✅ Validation
        if not email or not password:
            return jsonify({"status": "error", "message": "Missing email or password"}), 400

        user = users_collection.find_one({"email": email})

        if user and bcrypt.check_password_hash(user.get("password", ""), password):
            session.permanent = True
            session["user_id"] = str(user["_id"])
            session["user_email"] = email
            session["user_name"] = user.get("first_name", email.split("@")[0])

            return jsonify({"status": "ok"})

        return jsonify({"status": "error", "message": "Invalid email or password"}), 401

    # GET request
    return render_template("login.html")


@app.route("/logout")
def logout():
    app.config["SESSION_PERMANENT"] = False
    session.clear()
    return redirect(url_for("intro"))


@app.route("/api/me", methods=["GET"])
@login_required
def api_me():
    return jsonify({
        "status": "ok",
        "firstName": session.get("user_name", "User"),
        "name":      session.get("user_name", "User"),
        "username":  session.get("user_name", "user").lower().replace(" ", ""),
        "user_email": session.get("user_email", ""),
    })


@app.route("/debug")
def debug():
    return jsonify({
        "session": dict(session),
        "user_id": session.get("user_id"),
        "user_name": session.get("user_name"),
        "user_email": session.get("user_email"),
    })

@app.route("/dashboard")
@login_required
def dashboard():
    """Dashboard endpoint - returns index.html with user context"""
    user_id = session.get("user_id")
    
    try:
        # Check if user has any data
        has_statement = statements_col.find_one({"user_id": user_id}) is not None
        has_manual_txns = manual_txns_col.find_one({"user_id": user_id}) is not None
        has_investments = investments_col.find_one({"user_id": user_id}) is not None
        
        log.info(f"Dashboard accessed by {session.get('user_email')}: " 
                 f"has_statement={has_statement}, has_manual_txns={has_manual_txns}, has_investments={has_investments}")
        
        return render_template(
            "D:\files\templates\index.html",
            user_name=session.get("user_name", "User"),
            user_email=session.get("user_email", ""),
            is_new_user=not (has_statement or has_manual_txns or has_investments),
        )
    except Exception as e:
        log.error(f"Dashboard error for user {session.get('user_email')}: {e}")
    try:
            return render_template(
                "index.html",
                user_name=session.get("user_name", "User"),
                user_email=session.get("user_email", ""),
                is_new_user=True,
            )
    except Exception as e2:
            log.error(f"Template render also failed: {e2}")
            return f"<h2>Dashboard loading error</h2><p>{e}</p>", 500 


# ═══════════════════════════════════════════════════════════════
# ─── NEW: User data state endpoint ──────────────────────────────
# Called by index.html on load to decide: show empty state OR
# restore the user's last uploaded statement results.
# ═══════════════════════════════════════════════════════════════
@app.route("/api/user-data", methods=["GET"])
@login_required
def get_user_data():
    """
    Returns the most recent statement analysis for the logged-in user,
    plus their saved investments and manual transactions.
    If no data exists (brand-new user), returns is_new_user=True so
    the frontend can show a clean empty dashboard with zero values.
    """
    user_id = session["user_id"]
    
    try:
        # Fetch latest statement result for this user
        statement = statements_col.find_one(
            {"user_id": user_id},
            {"_id": 0},
            sort=[("uploaded_at", -1)],   # most recent first
        )

        # Fetch all saved investments
        investments = list(investments_col.find({"user_id": user_id}, {"_id": 0}))
        
        # Fetch all manual transactions
        manual_txns = list(manual_txns_col.find({"user_id": user_id}, {"_id": 0}))

        # NEW USER: Show zero dashboard
        if not statement and not investments and not manual_txns:
            log.info(f"New user detected: {session.get('user_email')}")
            return jsonify({
                "status":      "ok",
                "is_new_user": True,
                "data": {
                    "total_income": 0,
                    "total_expense": 0,
                    "net_savings": 0,
                    "savings_rate": 0,
                    "health_score": 50,  # Default score for new users
                    "transaction_count": 0,
                    "categories": [],
                    "monthly_trend": {},
                    "top_merchants": [],
                    "money_leaks": [],
                    "anomalies": [],
                    "investments": [],
                    "manual_transactions_count": 0,
                },
                "message": "Welcome! Upload your first bank statement to get started.",
            })

        # RETURNING USER: Show their data merged with manual transactions
        if statement:
            # Merge manual transactions with statement data if needed
            if manual_txns:
                # Add manual transactions to statement for merged view
                statement["manual_transactions_count"] = len(manual_txns)
                statement["has_manual_data"] = True
            else:
                statement["manual_transactions_count"] = 0
                statement["has_manual_data"] = False
        
        log.info(f"Returning user data: {session.get('user_email')} "
                f"(statements: {1 if statement else 0}, investments: {len(investments)}, "
                f"manual_txns: {len(manual_txns)})")
        
        return jsonify({
            "status":      "ok",
            "is_new_user": False,
            "data": statement or {},
            "investments": investments,
            "manual_transactions": manual_txns,
            "message":     "Your financial data loaded successfully",
        })
        
    except Exception as e:
        log.error(f"Error in get_user_data: {e}", exc_info=True)
        return jsonify({
            "status":  "error",
            "message": "Failed to load user data",
            "is_new_user": True,  # Safe fallback to empty state
            "data": {
                "total_income": 0,
                "total_expense": 0,
                "net_savings": 0,
                "savings_rate": 0,
                "health_score": 50,
                "transaction_count": 0,
                "categories": [],
                "monthly_trend": {},
            }
        }), 500


# ═══════════════════════════════════════════════════════════════
# ─── GOOGLE OAUTH ───────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════

@app.route("/login/google")
def google_login():
    redirect_uri = url_for("google_authorize", _external=True)
    nonce = secrets.token_urlsafe(16)
    session["oauth_nonce"] = nonce
    return google.authorize_redirect(redirect_uri, nonce=nonce)


@app.route("/login/google/authorize")
def google_authorize():
    try:
        token = google.authorize_access_token()
        nonce = session.pop("oauth_nonce", None)

        user_info = None

        # FIX: try id_token first, then userinfo endpoint, then token dict
        try:
            user_info = google.parse_id_token(token, nonce=nonce)
        except Exception as e:
            log.warning("parse_id_token failed (%s), falling back to userinfo endpoint", e)

        if not user_info:
            try:
                resp      = google.get("https://openidconnect.googleapis.com/v1/userinfo",
                                       token=token)
                user_info = resp.json()
            except Exception as e:
                log.error("userinfo endpoint also failed: %s", e)

        if not user_info:
            user_info = token.get("userinfo") or {}

        if not user_info or not user_info.get("email"):
            flash("Google login failed — could not retrieve your email. Please try again.", "error")
            return redirect(url_for("login"))

        email = user_info["email"].lower()
        user  = users_collection.find_one({"email": email})

        if not user:
            result = users_collection.insert_one({
                "first_name": user_info.get("given_name",  ""),
                "last_name":  user_info.get("family_name", ""),
                "email":      email,
                "auth_type":  "google",
                "created_at": datetime.now(timezone.utc),
            })
            user_id = str(result.inserted_id)
        else:
            user_id = str(user["_id"])

        session["user_id"]    = user_id
        session["user_email"] = email
        session["user_name"]  = user_info.get("given_name", email.split("@")[0])

        log.info("Google login success: %s", email)
        return redirect(url_for("dashboard"))

    except Exception as e:
        log.error("Google OAuth error: %s", e)
        flash(f"Google login error: {e}", "error")
        return redirect(url_for("login"))


# ═══════════════════════════════════════════════════════════════
# ─── API: Google Token Verification ─────────────────────────────
# Frontend sends Google credential, backend verifies and sets session
# ═══════════════════════════════════════════════════════════════
@app.route("/api/google-verify", methods=["POST"])
def api_google_verify():
    """Verify Google ID token from frontend and set session"""
    try:
        data = request.get_json()
        credential = data.get("credential")
        
        if not credential:
            return jsonify({"status": "error", "message": "No credential provided"}), 400
        
        # Verify ID token with Google
        try:
            id_info = google.parse_id_token({"id_token": credential})
        except Exception as e:
            log.warning(f"ID token parse failed: {e}, trying userinfo endpoint")
            return jsonify({"status": "error", "message": "Token verification failed"}), 401
        
        if not id_info or not id_info.get("email"):
            return jsonify({"status": "error", "message": "No email in token"}), 401
        
        email = id_info["email"].lower()
        user = users_collection.find_one({"email": email})
        
        # Create new user if doesn't exist
        if not user:
            result = users_collection.insert_one({
                "first_name": id_info.get("given_name", ""),
                "last_name": id_info.get("family_name", ""),
                "email": email,
                "auth_type": "google",
                "created_at": datetime.now(timezone.utc),
            })
            user_id = str(result.inserted_id)
        else:
            user_id = str(user["_id"])
        
        # Set session
        session["user_id"] = user_id
        session["user_email"] = email
        session["user_name"] = id_info.get("given_name", email.split("@")[0])
        
        log.info(f"✓ Google API login success: {email}")
        return jsonify({"status": "ok", "message": "Authenticated"}), 200
    
    except Exception as e:
        log.error(f"Google API verification error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/google-verify-token", methods=["POST"])
def api_google_verify_token():
    """Verify Google access token from frontend and set session"""
    try:
        data = request.get_json()
        access_token = data.get("access_token")
        
        if not access_token:
            return jsonify({"status": "error", "message": "No access token provided"}), 400
        
        # Get user info from Google with access token
        try:
            import requests
            resp = requests.get(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=5
            )
            if resp.status_code != 200:
                return jsonify({"status": "error", "message": "Failed to get user info"}), 401
            
            user_info = resp.json()
        except Exception as e:
            log.error(f"Failed to fetch userinfo: {e}")
            return jsonify({"status": "error", "message": "Failed to get user info"}), 401
        
        if not user_info or not user_info.get("email"):
            return jsonify({"status": "error", "message": "No email in user info"}), 401
        
        email = user_info["email"].lower()
        user = users_collection.find_one({"email": email})
        
        # Create new user if doesn't exist
        if not user:
            result = users_collection.insert_one({
                "first_name": user_info.get("given_name", ""),
                "last_name": user_info.get("family_name", ""),
                "email": email,
                "auth_type": "google",
                "created_at": datetime.now(timezone.utc),
            })
            user_id = str(result.inserted_id)
        else:
            user_id = str(user["_id"])
        
        # Set session
        session["user_id"] = user_id
        session["user_email"] = email
        session["user_name"] = user_info.get("given_name", email.split("@")[0])
        
        log.info(f"✓ Google token login success: {email}")
        return jsonify({"status": "ok", "message": "Authenticated"}), 200
    
    except Exception as e:
        log.error(f"Google token verification error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500



# ═══════════════════════════════════════════════════════════════
# ─── API ROUTES ─────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════

@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "status":          "ok",
        "version":         "2.3.0",
        "ml":              "TF-IDF + Naive Bayes",
        "groq":            GROQ_OK,
        "allowed_formats": list(Config.ALLOWED_EXTENSIONS),
    })


@app.route("/api/analyze-statement", methods=["POST"])
@login_required
def analyze_statement():
    if "file" not in request.files:
        return jsonify({"status": "error", "message": "No file provided"}), 400

    file = request.files["file"]
    if not file or file.filename == "":
        return jsonify({"status": "error", "message": "No file selected"}), 400

    filename = file.filename
    if not allowed_file(filename):
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "unknown"
        return jsonify({
            "status":  "error",
            "message": f"File type '.{ext}' not supported. Please upload a CSV or PDF file only.",
            "allowed": list(Config.ALLOWED_EXTENSIONS),
        }), 415

    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", filename)
    filepath  = os.path.join(Config.UPLOAD_FOLDER, safe_name)
    file.save(filepath)
    log.info("Uploaded: %s (%.1f KB)", safe_name, os.path.getsize(filepath) / 1024)

    ext          = safe_name.rsplit(".", 1)[1].lower()
    transactions = parse_csv(filepath) if ext == "csv" else parse_pdf(filepath)

    try:
        os.remove(filepath)
    except Exception:
        pass

    if not transactions:
        return jsonify({
            "status":  "error",
            "message": (
                "Could not extract transactions from this file. "
                "Ensure it's a valid bank statement with date, description, and amount columns."
            ),
        }), 422

    result           = analyse_statement(transactions, filename)
    result["status"] = "success"

    # ── Persist statement result + investments to MongoDB ────────
    user_id = session.get("user_id")
    if user_id:
        # Save the full statement analysis so it can be restored on next login
        saveable = {k: v for k, v in result.items() if k != "transactions"}
        saveable["user_id"]     = user_id
        saveable["uploaded_at"] = datetime.now(timezone.utc)
        
        # Insert as new history entry (keep all uploads)
        statements_col.insert_one(saveable)
        log.info("✓ Statement saved to history for user %s", user_id)

        # Save auto-extracted investments
        if result.get("investments"):
            investments_col.delete_many({"user_id": user_id, "source": "statement"})
            docs = [{**inv, "user_id": user_id} for inv in result["investments"]]
            if docs:
                investments_col.insert_many(docs)
                log.info("Saved %d auto-investments for user %s", len(docs), user_id)

    log.info("Parsed %d transactions, %d investments from %s",
             len(transactions), len(result.get("investments", [])), filename)
    return jsonify(result)


@app.route("/api/investments", methods=["GET"])
@login_required
def get_investments():
    user_id = session["user_id"]
    docs = list(investments_col.find({"user_id": user_id}, {"_id": 0}))
    return jsonify({"status": "ok", "investments": docs})


@app.route("/api/investments", methods=["POST"])
@login_required
def save_investment():
    body    = request.get_json(silent=True) or {}
    user_id = session["user_id"]
    inv = {
        "id":      body.get("id", f"man_{secrets.token_hex(4)}"),
        "name":    body.get("name", "Investment"),
        "type":    body.get("type", "SIP"),
        "amt":     float(body.get("amt", 0)),
        "ret":     float(body.get("ret", 12)),
        "risk":    body.get("risk", "med"),
        "horizon": body.get("horizon", "5 years"),
        "source":  "manual",
        "user_id": user_id,
    }
    investments_col.update_one(
        {"user_id": user_id, "id": inv["id"]},
        {"$set": inv},
        upsert=True,
    )
    return jsonify({"status": "ok", "investment": inv})


@app.route("/api/investments/<inv_id>", methods=["DELETE"])
@login_required
def delete_investment(inv_id):
    user_id = session["user_id"]
    investments_col.delete_one({"user_id": user_id, "id": inv_id})
    return jsonify({"status": "ok"})


@app.route("/api/advisor",  methods=["POST"])
@app.route("/api/chat",     methods=["POST"])
def advisor():
    body     = request.get_json(silent=True) or {}
    query    = body.get("query",   "").strip()
    history  = body.get("history", [])
    system   = body.get("system",  "You are BudgetAI, a warm Indian personal finance advisor.")
    messages = body.get("messages", [])

    if not query and messages:
        query   = messages[-1].get("content", "") if messages else ""
        history = [m for m in messages[:-1] if isinstance(m, dict)]

    if query.upper() in ("GREET", "__GREET__", ""):
        greeting = ask_groq(system, [], (
            "Greet the user warmly as BudgetAI. "
            "Introduce yourself in 2 sentences. "
            "Then ask them 3 short questions on separate lines to understand their finances: "
            "1) their monthly income, 2) their biggest expense category, "
            "3) their main financial goal (save more / pay off debt / invest). "
            "Be friendly, use Indian context (₹, SIP, EMI). Keep it under 100 words."
        ))
        return jsonify({"status": "ok", "answer": greeting, "reply": greeting})

    if not query:
        return jsonify({"status": "error", "message": "Empty query"}), 400

    answer = ask_groq(system, history, query)
    return jsonify({"status": "ok", "answer": answer, "reply": answer})


@app.route("/api/greet", methods=["POST"])
def greet():
    body    = request.get_json(silent=True) or {}
    context = body.get("context", "")
    system  = body.get("system",  "You are BudgetAI, a warm Indian personal finance advisor.")
    prompt  = (
        f"Greet the user warmly. {context} "
        "Then ask 3 short numbered questions to understand their goals: "
        "income level, biggest worry about money, and what they want to achieve this year. "
        "Be concise, friendly, use ₹ and Indian context. Under 120 words."
    )
    answer = ask_groq(system, [], prompt)
    return jsonify({"status": "ok", "answer": answer, "reply": answer})


@app.route("/api/categorise", methods=["POST"])
def categorise():
    body = request.get_json(silent=True) or {}
    desc = body.get("description", "")
    if not desc:
        return jsonify({"status": "error", "message": "description required"}), 400
    return jsonify({"status": "ok", "category": smart_categorise(desc), "description": desc})


@app.route("/api/batch-categorise", methods=["POST"])
def batch_categorise():
    body         = request.get_json(silent=True) or {}
    descriptions = body.get("descriptions", [])[:500]
    if not descriptions:
        return jsonify({"status": "error", "message": "descriptions list required"}), 400
    categories = [smart_categorise(d) for d in descriptions]
    return jsonify({
        "status":  "ok",
        "results": [{"description": d, "category": c}
                    for d, c in zip(descriptions, categories)],
    })


# ═══════════════════════════════════════════════════════════════
# ─── NEW: Manual Transactions (Auto-Update Feature) ────────────
# NOTE: manual_txns_col is already defined in init_mongodb() — do not redefine hereby users

@app.route("/api/transactions/add", methods=["POST"])
@login_required
def add_manual_transaction():
    """Add a manual transaction (Cash, UPI, etc.) and auto-update dashboard"""
    body    = request.get_json(silent=True) or {}
    user_id = session["user_id"]
    
    # Parse the transaction data
    desc     = body.get("description", "").strip()
    amount   = float(body.get("amount", 0))
    txn_type = body.get("type", "DEBIT").upper()  # DEBIT or CREDIT
    category = body.get("category", "Other").strip()
    date_str = body.get("date", date.today().strftime("%Y-%m-%d"))
    payment_method = body.get("payment_method", "Cash")
    
    if not desc or amount <= 0:
        return jsonify({"status": "error", "message": "Description and amount required"}), 400
    
    if txn_type not in ("DEBIT", "CREDIT"):
        return jsonify({"status": "error", "message": "Type must be DEBIT or CREDIT"}), 400
    
    # Auto-categorize if not provided
    if category == "Other":
        category = smart_categorise(desc)
    
    # Create transaction object
    txn = {
        "id":              secrets.token_hex(8),
        "user_id":         user_id,
        "description":     desc[:80],
        "amount":          round(amount, 2),
        "type":            txn_type,
        "category":        category,
        "date":            date_str,
        "payment_method":  payment_method,
        "source":          "manual",
        "created_at":      datetime.utcnow(),
    }
    
    # Save to manual transactions collection
    result = manual_txns_col.insert_one(txn)
    txn["_id"] = str(result.inserted_id)
    
    log.info(f"✓ Manual transaction added for user {user_id}: {desc}")
    
    # Return updated transaction with ID
    return jsonify({
        "status":      "ok",
        "transaction": {k: v for k, v in txn.items() if k != "_id"},
        "message":     f"Transaction '{desc}' added! Dashboard will auto-update.",
    })


@app.route("/api/transactions", methods=["GET"])
@login_required
def get_manual_transactions():
    """Get all manual transactions for the user"""
    user_id = session["user_id"]
    txns = list(manual_txns_col.find(
        {"user_id": user_id},
        {"_id": 0}
    ).sort("created_at", -1))
    
    return jsonify({
        "status":       "ok",
        "transactions": txns,
        "count":        len(txns),
    })


@app.route("/api/transactions/<txn_id>", methods=["DELETE"])
@login_required
def delete_manual_transaction(txn_id):
    """Delete a manual transaction"""
    user_id = session["user_id"]
    result = manual_txns_col.delete_one({
        "user_id": user_id,
        "id":      txn_id,
    })
    
    if result.deleted_count == 0:
        return jsonify({"status": "error", "message": "Transaction not found"}), 404
    
    log.info(f"✓ Manual transaction deleted for user {user_id}")
    return jsonify({"status": "ok", "message": "Transaction deleted"})


@app.route("/api/dashboard/merged", methods=["GET"])
@login_required
def get_merged_dashboard():
    """
    Get dashboard data MERGED with manual transactions.
    Combines statement data + manual transactions for a complete view.
    """
    user_id = session["user_id"]
    
    # Get latest statement
    statement = statements_col.find_one(
        {"user_id": user_id},
        {"_id": 0},
        sort=[("uploaded_at", -1)],
    )
    
    if not statement:
        return jsonify({
            "status":      "error",
            "message":     "No statement uploaded yet",
            "is_new_user": True,
        }), 404
    
    # Get manual transactions
    manual_txns = list(manual_txns_col.find(
        {"user_id": user_id},
        {"_id": 0}
    ))
    
    # Merge transactions: statement + manual
    all_transactions = statement.get("transactions", []) + manual_txns
    
    # Re-analyze with merged transactions
    merged_result = analyse_statement(all_transactions, statement.get("filename", "merged"))
    merged_result["manual_transactions_count"] = len(manual_txns)
    merged_result["source"] = "merged"
    
    return jsonify({
        "status":  "ok",
        "data":    merged_result,
        "message": f"Dashboard updated with {len(manual_txns)} manual transactions",
    })


# ═══════════════════════════════════════════════════════════════
# ─── NEW: Statement Comparison Feature ──────────────────────────
# ═══════════════════════════════════════════════════════════════

@app.route("/api/statements/compare", methods=["POST"])
@login_required
def compare_statements():
    """Compare two statements side-by-side"""
    user_id = session["user_id"]
    body = request.get_json(silent=True) or {}
    
    file1_idx = body.get("file1_idx", 0)  # Index of first statement (0 = latest)
    file2_idx = body.get("file2_idx", 1)  # Index of second statement
    
    # Get all statements
    all_statements = list(statements_col.find(
        {"user_id": user_id},
        {"_id": 0}
    ).sort("uploaded_at", -1))
    
    if len(all_statements) < 2:
        return jsonify({
            "status":  "error",
            "message": "Need at least 2 uploaded statements to compare",
        }), 400
    
    stmt1 = all_statements[file1_idx] if file1_idx < len(all_statements) else None
    stmt2 = all_statements[file2_idx] if file2_idx < len(all_statements) else None
    
    if not stmt1 or not stmt2:
        return jsonify({"status": "error", "message": "Invalid statement indices"}), 400
    
    # Build comparison metrics
    comparison = {
        "statement_1": {
            "filename":           stmt1.get("filename", "Unknown"),
            "date":               str(stmt1.get("uploaded_at", "")),
            "total_income":       stmt1.get("total_income", 0),
            "total_expense":      stmt1.get("total_expense", 0),
            "net_savings":        stmt1.get("net_savings", 0),
            "health_score":       stmt1.get("health_score", 0),
            "transaction_count":  stmt1.get("transaction_count", 0),
        },
        "statement_2": {
            "filename":           stmt2.get("filename", "Unknown"),
            "date":               str(stmt2.get("uploaded_at", "")),
            "total_income":       stmt2.get("total_income", 0),
            "total_expense":      stmt2.get("total_expense", 0),
            "net_savings":        stmt2.get("net_savings", 0),
            "health_score":       stmt2.get("health_score", 0),
            "transaction_count":  stmt2.get("transaction_count", 0),
        },
        "difference": {
            "income_change":      stmt2.get("total_income", 0) - stmt1.get("total_income", 0),
            "expense_change":     stmt2.get("total_expense", 0) - stmt1.get("total_expense", 0),
            "savings_change":     stmt2.get("net_savings", 0) - stmt1.get("net_savings", 0),
            "health_score_change": stmt2.get("health_score", 0) - stmt1.get("health_score", 0),
        },
        "category_comparison": {},
        "trend_comparison": {},
    }
    
    # Compare categories
    cats1 = {c["category"]: c["amount"] for c in stmt1.get("categories", [])}
    cats2 = {c["category"]: c["amount"] for c in stmt2.get("categories", [])}
    
    all_cats = set(list(cats1.keys()) + list(cats2.keys()))
    for cat in sorted(all_cats):
        amt1 = cats1.get(cat, 0)
        amt2 = cats2.get(cat, 0)
        comparison["category_comparison"][cat] = {
            "amount_1": amt1,
            "amount_2": amt2,
            "difference": amt2 - amt1,
            "percent_change": round(((amt2 - amt1) / amt1 * 100) if amt1 > 0 else 0, 1),
        }
    
    # Compare monthly trends
    trend1 = stmt1.get("monthly_trend", {})
    trend2 = stmt2.get("monthly_trend", {})
    
    for month in sorted(set(list(trend1.keys()) + list(trend2.keys()))):
        m1 = trend1.get(month, {"income": 0, "expense": 0})
        m2 = trend2.get(month, {"income": 0, "expense": 0})
        comparison["trend_comparison"][month] = {
            "income_1":  m1.get("income", 0),
            "expense_1": m1.get("expense", 0),
            "income_2":  m2.get("income", 0),
            "expense_2": m2.get("expense", 0),
        }
    
    log.info(f"✓ Comparison generated for user {user_id}")
    return jsonify({
        "status":      "ok",
        "comparison":  comparison,
        "message":     "Comparison ready",
    })


# ═══════════════════════════════════════════════════════════════
# ─── HISTORY & EXPORT ENDPOINTS ─────────────────────────────────
# ═══════════════════════════════════════════════════════════════

@app.route("/api/statements-history", methods=["GET"])
@login_required
def get_statements_history():
    """Get all statements history for user with optional filtering"""
    user_id = session["user_id"]
    filter_type = request.args.get("type", "all")  # all, month, year, quarter
    filter_value = request.args.get("value", "")    # e.g., "2024-01", "2024", "Q1-2024"
    
    # Get all statements for this user
    statements = list(statements_col.find(
        {"user_id": user_id},
        {"_id": 0}
    ).sort("uploaded_at", -1))
    
    if not statements:
        return jsonify({"status": "ok", "history": [], "total": 0})
    
    # Build history with month/year/quarter grouping
    history_map = {}
    for stmt in statements:
        uploaded_at = stmt.get("uploaded_at")
        if isinstance(uploaded_at, str):
            uploaded_at = datetime.fromisoformat(uploaded_at)
        elif not isinstance(uploaded_at, datetime):
            uploaded_at = datetime.utcnow()
        
        filename = stmt.get("filename", "Unknown")
        
        # Create summary
        summary = {
            "filename": filename,
            "uploaded_at": uploaded_at.isoformat(),
            "transaction_count": stmt.get("transaction_count", 0),
            "total_income": stmt.get("total_income", 0),
            "total_expense": stmt.get("total_expense", 0),
            "net_savings": stmt.get("net_savings", 0),
            "savings_rate": stmt.get("savings_rate", 0),
            "health_score": stmt.get("health_score", 0),
        }
        
        # Group by time period
        year = uploaded_at.year
        month = uploaded_at.month
        quarter = (month - 1) // 3 + 1
        
        month_key = f"{year}-{month:02d}"
        year_key = str(year)
        quarter_key = f"Q{quarter}-{year}"
        
        for key in [month_key, year_key, quarter_key]:
            if key not in history_map:
                history_map[key] = []
            if summary not in history_map[key]:
                history_map[key].append(summary)
    
    # Apply filter if requested
    result = history_map
    if filter_type != "all" and filter_value:
        if filter_type == "month" and filter_value in history_map:
            result = {filter_value: history_map[filter_value]}
        elif filter_type == "year" and filter_value in history_map:
            result = {filter_value: history_map[filter_value]}
        elif filter_type == "quarter" and filter_value in history_map:
            result = {filter_value: history_map[filter_value]}
    
    return jsonify({
        "status": "ok",
        "history": result,
        "total": len(statements),
        "statements": statements[:12]  # Latest 12 statements
    })


@app.route("/api/export-pdf", methods=["GET"])
@login_required
def export_pdf():
    """Generate and download PDF report of current dashboard"""
    user_id = session["user_id"]
    user_name = session.get("user_name", "User")
    user_email = session.get("user_email", "")
    
    # Get the most recent statement
    statement = statements_col.find_one(
        {"user_id": user_id},
        sort=[("uploaded_at", -1)]
    )
    
    if not statement:
        return jsonify({
            "status": "error",
            "message": "No statement data found. Please upload a statement first."
        }), 404
    
    # Remove MongoDB ID field
    analysis_result = {k: v for k, v in statement.items() if k != "_id"}
    
    # Generate PDF
    pdf_buffer = generate_pdf_report(analysis_result, user_name, user_email)
    
    if not pdf_buffer:
        return jsonify({
            "status": "error",
            "message": "Failed to generate PDF"
        }), 500
    
    # Return PDF as file download
    return send_file(
        pdf_buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name="dashboard_report.pdf"
    )


@app.route("/api/export-pdf/download", methods=["GET"])
@login_required
def export_pdf_download():
    """Generate and return PDF as downloadable file"""
    user_id = session["user_id"]
    user_name = session.get("user_name", "User")
    user_email = session.get("user_email", "")
    
    statement = statements_col.find_one(
        {"user_id": user_id},
        sort=[("uploaded_at", -1)]
    )
    
    if not statement:
        return jsonify({
            "status": "error",
            "message": "No statement found"
        }), 404
    
    analysis_result = {k: v for k, v in statement.items() if k != "_id"}
    pdf_buffer = generate_pdf_report(analysis_result, user_name, user_email)
    
    if not pdf_buffer:
        return jsonify({"status": "error", "message": "PDF generation failed"}), 500
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"BudgetAI_Dashboard_{timestamp}.pdf"
    
    return send_file(
        pdf_buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename
    )


# ═══════════════════════════════════════════════════════════════
# STATIC FILE SERVING  (catch-all — must be last)
# ═══════════════════════════════════════════════════════════════
FRONTEND_DIR = os.path.dirname(os.path.abspath(__file__))

@app.route("/<path:filename>")
def serve_static(filename):
    # Try root directory first
    filepath = os.path.join(FRONTEND_DIR, filename)
    if os.path.exists(filepath):
        return send_from_directory(FRONTEND_DIR, filename)
    
    # Then try templates folder
    templates_dir = os.path.join(FRONTEND_DIR, "templates")
    filepath = os.path.join(templates_dir, filename)
    if os.path.exists(filepath):
        return send_from_directory(templates_dir, filename)
    
    return "Not found", 404


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 60)
    print("  BudgetAI Backend v2.3")
    print(f"  👉  Open http://localhost:{Config.PORT} in your browser")
    print("  ML  : TF-IDF + Naive Bayes  |  Formats: CSV, PDF")
    print(f"  Groq: {'✅ Enabled' if GROQ_OK else '❌ Key missing'}")
    print("  Templates: ./templates/")
    print("=" * 60)
    app.run(host=Config.HOST, port=Config.PORT, debug=Config.DEBUG)