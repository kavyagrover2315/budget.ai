# 🏗️ Architecture & Data Flow Diagrams

## 1. Authentication & User Data Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                    USER AUTHENTICATION FLOW                      │
└─────────────────────────────────────────────────────────────────┘

SIGNUP FLOW:
┌──────────┐      ┌────────────────┐      ┌──────────────┐
│  User    │      │   Flask App    │      │  MongoDB     │
│ (Browser)│      │   (Backend)    │      │ (users col)  │
└────┬─────┘      └────────┬────────┘      └──────┬───────┘
     │                     │                      │
     │─ POST /signup ──────>│                      │
     │                     │─ Check email ────────>│
     │                     │<─ OK (not found) ─────│
     │                     │                      │
     │                     │─ INSERT user ────────>│
     │                     │<─ Confirmed ──────────│
     │<─ Redirect login ────│                      │
     │                     │                      │

LOGIN FLOW:
┌──────────┐      ┌────────────────┐      ┌──────────────────────┐
│  User    │      │   Flask App    │      │ Supabase / MongoDB   │
│ (Browser)│      │   (Backend)    │      │ (users collection)   │
└────┬─────┘      └────────┬────────┘      └──────┬───────────────┘
     │                     │                      │
     │─ POST /login ──────>│                      │
     │                     │─ Try Supabase first ─>│
     │                     │<─ user found ─────────│ (or attempt fails)
     │                     │                      │
     │                     │─ Fallback MongoDB ──>│
     │                     │<─ user found ─────────│
     │                     │                      │
     │                     │─ Verify password     │
     │                     │─ CREATE session      │
     │<─ Redirect dashboard│                      │
     │                     │                      │

NEW USER FIRST LOGIN:
┌──────────┐      ┌────────────────┐      ┌──────────────────────┐
│ Browser  │      │   /dashboard   │      │ /api/user-data       │
└────┬─────┘      └────────┬────────┘      └──────┬───────────────┘
     │                     │                      │
     │─ GET /dashboard ───>│                      │
     │<─ Render index.html──│                      │
     │                     │                      │
     │─ loadUserData() ────>│ GET /api/user-data  │
     │                     │────────────────────>│
     │                     │  Check MongoDB:     │
     │                     │  - statements?  NO  │
     │                     │  - investments? NO  │
     │                     │  - manual_txns? NO  │
     │                     │                     │
     │                     │<─ { is_new_user: true,│
     │<─ Zero Dashboard ────│    data: { income:0,│
     │                     │    expense:0, ... } │
     │                     │                     │
```

---

## 2. Data Persistence Architecture

```
┌────────────────────────────────────────────────────────────┐
│              DATABASE COLLECTIONS SCHEMA                    │
└────────────────────────────────────────────────────────────┘

Users Collection:
┌─────────────────────────────────────┐
│ _id (ObjectId)                      │
│ email (unique index) ◄── Fast lookup│
│ password (hashed)                   │
│ first_name, last_name               │
│ created_at, updated_at              │
└─────────────────────────────────────┘

Statements Collection:
┌──────────────────────────────────┐
│ _id (ObjectId)                   │
│ user_id (index) ◄── Fast lookup   │
│ filename                          │
│ total_income                      │
│ total_expense                     │
│ net_savings                       │
│ health_score                      │
│ categories, monthly_trend         │
│ money_leaks, anomalies            │
│ investments                       │
│ transactions (embedded)           │
│ uploaded_at                       │
└──────────────────────────────────┘

Manual Transactions Collection:
┌──────────────────────────────────┐
│ _id (ObjectId)                   │
│ user_id (index) ◄── Fast lookup   │
│ description                       │
│ amount                            │
│ type (DEBIT/CREDIT)               │
│ category (auto-assigned)          │
│ date                              │
│ payment_method                    │
│ source: "manual"                  │
│ created_at                        │
└──────────────────────────────────┘

Investments Collection:
┌──────────────────────────────────┐
│ _id (ObjectId)                   │
│ user_id (index) ◄── Fast lookup   │
│ name, type, amount                │
│ return %, risk level              │
│ horizon, source                   │
└──────────────────────────────────┘

Indexes Created on Startup:
✓ users.email (unique)
✓ statements.user_id
✓ manual_transactions.user_id
✓ investments.user_id
→ All queries O(1) instead of O(n)
```

---

## 3. API Endpoint Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    API ENDPOINTS HIERARCHY                   │
└─────────────────────────────────────────────────────────────┘

AUTH ENDPOINTS:
  POST   /signup              → Create account
  POST   /login               → User login
  GET    /logout              → Clear session
  POST   /login/google        → Google OAuth

DASHBOARD ENDPOINTS:
  GET    /dashboard           → Render HTML (new_user param)
  GET    /api/user-data       → New user detection
  GET    /api/dashboard/merged → Merged data with manual txns

STATEMENT ENDPOINTS:
  POST   /api/analyze-statement    → Upload & analyze
  GET    /api/statements-history   → Get all user statements
  POST   /api/statements/compare   → Compare two statements

TRANSACTION ENDPOINTS:
  POST   /api/transactions/add     → Add manual transaction
  GET    /api/transactions         → List manual transactions
  DELETE /api/transactions/<id>    → Delete transaction

INVESTMENT ENDPOINTS:
  GET    /api/investments          → List investments

MONITORING ENDPOINTS:
  GET    /api/health               → Service status
  POST   /api/greet                → AI greeting


FALLBACK LOGIC:
┌─────────────────┐
│ Try Supabase    │──NO──┐
└────────┬────────┘      │
         │               │
        YES              ▼
         │      ┌─────────────────┐
         │      │ Use MongoDB     │
         └─────>│ (primary DB)    │
                └─────────────────┘
```

---

## 4. New User Experience Timeline

```
TIME    EVENT                           STATE
─────   ──────────────────────────────  ─────────────────────────
0s      User visits signup page         "Create Account" form

5s      User submits signup             Validating...

10s     Account created                 ✓ "Go to login"

15s     User logs in                    Authenticating...

20s     Dashboard page loads            Loading data...

25s     /api/user-data called           Checking if new user...

30s     Response: is_new_user=true     Rendering zero dashboard

30-35s  Dashboard visible               ✓ Income: ₹0
                                        ✓ Expense: ₹0
                                        ✓ Savings: ₹0
                                        ✓ Health: 50/100
                                        ✓ "Upload Statement" 🚀

40s     User uploads statement          Processing...

50-60s  Statement analyzed              ✓ Extracted 125 txns

65s     Dashboard updates               Income: ₹85,000
                                        Expense: ₹52,500
                                        Savings: ₹32,500
                                        Health: 78/100
                                        ✓ Charts visible
```

---

## 5. Deployment Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                 PRODUCTION DEPLOYMENT STACK                  │
└─────────────────────────────────────────────────────────────┘

        ┌──────────────────────────────┐
        │   User's Browser/Client      │
        └───────────┬──────────────────┘
                    │ HTTPS
                    ▼
        ┌──────────────────────────────┐
        │   nginx/Reverse Proxy        │  (Optional)
        │   (Load Balancing)           │
        └───────────┬──────────────────┘
                    │
                    ▼
        ┌──────────────────────────────┐
        │  Gunicorn Workers (4x)       │
        │  Flask Application           │
        │  (app.py)                    │
        └───────────┬──────────────────┘
                    │
        ┌───────────┴──────────────────┐
        │                              │
        ▼                              ▼
    ┌────────────┐              ┌──────────────┐
    │  MongoDB   │              │  Supabase    │
    │  Atlas     │              │  (Optional)  │
    │ (Primary)  │              │  (Fallback)  │
    └────────────┘              └──────────────┘

┌─────────────────────────────────────────┐
│         ENVIRONMENT VARIABLES (.env)    │
│  ✓ MONGO_URI (critical)                 │
│  ✓ FLASK_SECRET_KEY (production value)  │
│  ✓ GOOGLE_CLIENT_ID/SECRET (optional)   │
│  ✓ GROQ_API_KEY (optional)              │
│  ✓ SUPABASE_URL/KEY (optional)          │
└─────────────────────────────────────────┘

MONITORING:
┌──────────────────────┐
│ /api/health (5min)   │
│ Application Logs     │
│ MongoDB Dashboard    │
│ Error Alerts         │
└──────────────────────┘
```

---

## 6. Error Handling Flow

```
┌─────────────────────────────────────────────────────────────┐
│                 ERROR HANDLING ARCHITECTURE                  │
└─────────────────────────────────────────────────────────────┘

ON STARTUP:
┌──────────────┐
│ validate_    │
│ config()     │
└──────┬───────┘
       │
       ├─ Is SECRET_KEY default?
       │  YES → ⚠ WARNING logged
       │  NO → OK
       │
       ├─ Is MONGO_URI set?
       │  NO → ✗ CRITICAL ERROR, app stops
       │  YES → Continue
       │
       ├─ init_mongodb()
       │  Connection fails → ✗ ERROR, app stops
       │  Connection OK → ✓ Continue
       │
       └─ Check optional services
          Missing → ⚠ WARNING logged
          Present → ✓ OK

DURING API REQUESTS:
┌──────────────────┐
│ API Request      │
└────────┬─────────┘
         │
         ▼
    ┌─────────────┐
    │ Try request │
    └────┬────────┘
         │
    ┌────┴──────────────┐
    │                   │
   YES                  NO
    │                   │
    ▼                   ▼
┌─────────────┐  ┌──────────────────┐
│ Log success │  │ Catch exception  │
│ Return data │  └────────┬─────────┘
└─────────────┘           │
                          ▼
                   ┌──────────────┐
                   │ Log error    │
                   │ with details │
                   └────────┬─────┘
                            │
                            ▼
                   ┌──────────────────┐
                   │ Return error JSON│
                   │ + HTTP status    │
                   │ + user message   │
                   └──────────────────┘

FRONTEND FALLBACK:
┌──────────────────────┐
│ loadUserData() call  │
└──────────┬───────────┘
           │
      ┌────┴──────────┐
      │               │
     YES             NO (error)
      │               │
      ▼               ▼
  ┌────────────┐  ┌──────────────────┐
  │ Load data  │  │ Show empty state │
  │ Render     │  │ (zero dashboard) │
  └────────────┘  │ Graceful fallback│
                  └──────────────────┘
```

---

## 7. Data Flow: Upload → Analysis → Dashboard

```
┌─────────────────────────────────────────────────────────────┐
│         STATEMENT UPLOAD TO DASHBOARD DATA FLOW              │
└─────────────────────────────────────────────────────────────┘

USER                API                  PROCESSING            STORAGE
────                ───                  ──────────            ───────

Upload CSV/PDF ──> POST 
                   /analyze-statement
                                    ┌─ Parse CSV/PDF
                                    │  Extract rows
                                    ├─ Detect columns
                                    │  (date, desc, amount)
                                    ├─ Clean amounts
                                    │  (remove ₹, commas)
                                    ├─ Classify type
                                    │  (CREDIT/DEBIT)
                                    ├─ Smart categorize
                                    │  (ML: TF-IDF+NB)
                                    │
                   <─ Transactions  ├─ Calculate totals
                     extracted      │  (income, expense)
                                    ├─ Detect anomalies
                                    │  (z-score)
                                    ├─ Detect money leaks
                                    │  (pattern matching)
                                    ├─ Calculate health
                                    │  (weighted formula)
                                    │
Receive <─────────  {status:"ok",  ├─ Build response      ──> INSERT
analysis         analysis_data}   │  JSON object        to statements_col
                                    │
                                    └─ Save investments  ──> INSERT
                                       auto-extracted      to investments_col

Render <─────────  Display 
dashboard        ├─ Charts (Chart.js)
                 ├─ Metrics cards
                 ├─ Transaction list
                 ├─ Category breakdown
                 ├─ Money leak alerts
                 └─ Health score gauge
```

---

## 8. Deployment Decision Tree

```
START DEPLOYMENT
       │
       ├─ Environment setup?
       │  ├─ NO → Create .env file
       │  └─ YES → Continue
       │
       ├─ Install dependencies?
       │  ├─ NO → pip install -r requirements.txt
       │  └─ YES → Continue
       │
       ├─ Test MongoDB?
       │  ├─ FAIL → Fix connection string
       │  └─ OK → Continue
       │
       ├─ Run app startup?
       │  ├─ FAIL → Check logs
       │  └─ OK → Continue
       │
       ├─ Verify /api/health?
       │  ├─ FAIL → MongoDB not responding
       │  └─ OK → Continue
       │
       ├─ Choose deployment method?
       │  ├─ Heroku? → git push heroku main
       │  ├─ AWS? → Configure, push, deploy
       │  ├─ Docker? → Build image, run container
       │  └─ VPS? → SSH, pull, install, run
       │
       ├─ Test new user flow?
       │  ├─ FAIL → Check frontend console
       │  └─ OK → Continue
       │
       └─ ✅ DEPLOYMENT COMPLETE
          Monitor /api/health
          Set up alerts
          Review logs daily
```

---

**Visual diagrams created for deployment planning and documentation.**  
**All flows show error handling and fallback mechanisms.**  
**Version 2.3.0 - Production Ready**
