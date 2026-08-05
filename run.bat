@echo off
echo =========================================
echo       OmniBridge - Quick Start
echo =========================================

echo.
echo [1/3] Checking environment...
if not exist venv (
    echo [!] Virtual environment not found. Creating one...
    python -m venv venv
)

echo [2/3] Activating virtual environment...
call venv\Scripts\activate

echo [!] Installing/Updating dependencies...
pip install -r requirements.txt -q

echo.
echo [3/3] Starting OmniBridge Server...
echo =========================================
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
pause
