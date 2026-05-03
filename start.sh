#!/bin/bash
# ════════════════════════════════════════════════
#  BudgetAI Backend — Start Script
# ════════════════════════════════════════════════

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "  ██████╗ ██╗   ██╗██████╗  ██████╗ ███████╗████████╗ █████╗ ██╗"
echo "  ██╔══██╗██║   ██║██╔══██╗██╔════╝ ██╔════╝╚══██╔══╝██╔══██╗██║"
echo "  ██████╔╝██║   ██║██║  ██║██║  ███╗█████╗     ██║   ███████║██║"
echo "  ██╔══██╗██║   ██║██║  ██║██║   ██║██╔══╝     ██║   ██╔══██║██║"
echo "  ██████╔╝╚██████╔╝██████╔╝╚██████╔╝███████╗   ██║   ██║  ██║██║"
echo "  ╚═════╝  ╚═════╝ ╚═════╝  ╚═════╝ ╚══════╝   ╚═╝   ╚═╝  ╚═╝╚═╝"
echo ""
echo "  Smart Financial Dashboard Backend v2.0"
echo "  ML: TF-IDF + Naive Bayes | Accepts: CSV, PDF only"
echo "────────────────────────────────────────────────────────────"

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "❌  Python 3 not found. Install it from https://python.org"
    exit 1
fi

# Install dependencies if needed
echo "📦  Checking dependencies..."
pip install -r requirements.txt -q --break-system-packages 2>/dev/null || pip install -r requirements.txt -q

# Groq API Key prompt
if [ -z "$GROQ_API_KEY" ]; then
    echo ""
    echo "⚠️  GROQ_API_KEY not set. AI Advisor will use fallback mode."
    echo "   Get a free key at: https://console.groq.com"
    echo "   Then run:  export GROQ_API_KEY=your_key_here"
    echo ""
fi

echo "🚀  Starting server on http://localhost:5000"
echo "────────────────────────────────────────────────────────────"
echo ""

# Open the HTML dashboard in browser (optional)
if command -v open &> /dev/null; then
    open index.html 2>/dev/null &
elif command -v xdg-open &> /dev/null; then
    xdg-open index.html 2>/dev/null &
fi

python3 app.py
