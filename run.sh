#!/bin/bash
# ======================================================================
#                   🐱 Meowtrix AI by Mohsin Khawaja
#           "100% Offline Multi-GGUF Local AI Studio (CPU)"
#                   Follow Instagram: @csib.mohsin
# ======================================================================

cd "$(dirname "$0")"

echo "======================================================================"
echo "                  🐱 Meowtrix AI by Mohsin Khawaja"
echo "          '100% Offline Multi-GGUF Local AI Studio (CPU)'"
echo "                  Follow Instagram: @csib.mohsin"
echo "======================================================================"
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "[ERROR] python3 could not be found. Please install Python 3.10+."
    exit 1
fi

mkdir -p models data

# Check dependencies
python3 -c "import llama_cpp, fastapi, uvicorn, pydantic" &> /dev/null
if [ $? -ne 0 ]; then
    echo "[SETUP] Installing dependencies from requirements.txt..."
    pip3 install -r requirements.txt
fi

echo "Starting Meowtrix AI on http://127.0.0.1:8000 ..."
python3 main.py

