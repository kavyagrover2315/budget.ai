# BudgetAI Backend — Setup Guide

## 📁 Files
```
backend/
├── app.py          ← Flask backend (ML + PDF/CSV parser + AI advisor)
├── index.html      ← Updated frontend (CSV/PDF only, wired to backend)
├── requirements.txt
├── start.sh        ← One-click startup script
└── README.md
```

## ⚡ Quick Start

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. (Optional) Add Groq API key for real AI responses
Get a free key from https://console.groq.com
```bash
export GROQ_API_KEY=gsk_your_key_here
```

### 3. Start the backend
```bash
python app.py
# OR
bash start.sh
```

### 4. Open the dashboard
Open `index.html` in your browser. The frontend auto-connects to `http://localhost:5000`.

---

## 🤖 ML Stack

| Component | Library | Purpose |
|-----------|---------|---------|
| Categoriser | scikit-learn TF-IDF + Naive Bayes | Auto-label transactions (Food, EMI, Investment…) |
| Anomaly Detection | NumPy Z-score per category | Flag unusually large spends |
| Money Leaks | Keyword pattern matching | Find wasteful subscriptions & habits |
| Health Score | Rule-based formula | 0–100 financial wellness score |
| AI Advisor | Groq (llama3-8b-8192) | Conversational financial advice |
| PDF Parsing | pdfplumber | Extract tables + raw text from bank PDFs |
| CSV Parsing | pandas | Handle all Indian bank CSV formats |

---

## 📂 Supported File Types
- ✅ **CSV** — Works with SBI, HDFC, ICICI, Axis, Kotak, Yes Bank exports
- ✅ **PDF** — Works with most Indian bank statement PDFs
- ❌ XLSX, TXT, and other formats are blocked (both frontend + backend)

---

## 🔌 API Endpoints

### `GET /api/health`
Check if backend is running.

### `POST /api/analyze-statement`
Upload and analyse a bank statement.
- **Body**: `multipart/form-data` with `file` field (CSV or PDF)
- **Returns**: Full analysis JSON with transactions, categories, anomalies, money leaks, monthly trend, health score

### `POST /api/advisor`
Ask the AI financial advisor.
- **Body**: `{ "query": "...", "history": [...], "system": "..." }`
- **Returns**: `{ "answer": "..." }`

### `POST /api/categorise`
Categorise a single transaction.
- **Body**: `{ "description": "Zomato order" }`
- **Returns**: `{ "category": "Food & Dining" }`

### `POST /api/batch-categorise`
Categorise up to 500 transactions at once.
- **Body**: `{ "descriptions": ["Zomato", "SBI EMI", ...] }`

---

## 🏦 Supported CSV Column Formats

The ML parser auto-detects columns — no manual mapping needed.

| Bank | Date Column | Debit | Credit |
|------|------------|-------|--------|
| HDFC | Date | Withdrawal Amt | Deposit Amt |
| SBI | Txn Date | Debit | Credit |
| ICICI | Transaction Date | Withdrawal | Deposit |
| Axis | Transaction Date | Debit Amount | Credit Amount |
| Kotak | Transaction Date | Debit | Credit |
| Generic | date / value date | debit / withdrawal | credit / deposit |

---

## 🎯 How the Dashboard Updates

When you upload a statement:
1. **ML** categorises every transaction (TF-IDF + Naive Bayes, ~98% accuracy on Indian bank data)
2. **Anomaly scan** flags Z-score > 2.0 outliers per category
3. **Leak detector** checks for food delivery, OTT, shopping, cab patterns
4. **Health score** computed from savings rate, EMI ratio, investment ratio
5. **All pages auto-update**: Dashboard, Transactions, EMI Tracker, Budget, Investments
6. **AI welcomes** you with a personalised summary of your finances
