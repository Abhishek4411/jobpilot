@echo off
echo ============================================
echo  JobPilot Setup Script (Windows)
echo ============================================

echo Creating virtual environment...
python -m venv venv
if errorlevel 1 (echo Failed to create venv. Is Python 3.10+ installed? & exit /b 1)

echo Activating virtual environment...
call venv\Scripts\activate

echo Installing Python dependencies...
pip install -r requirements.txt
if errorlevel 1 (echo pip install failed. Check your internet connection. & exit /b 1)

echo Installing Playwright browser (Chromium)...
playwright install chromium
if errorlevel 1 (echo Playwright install failed. Try running manually: playwright install chromium)

echo Creating data directories...
if not exist "data\logs" mkdir "data\logs"
if not exist "data\audio" mkdir "data\audio"
if not exist "data\resumes" mkdir "data\resumes"

echo Setting up environment file...
if not exist ".env" (
    copy .env.example .env
    echo Created .env from .env.example
) else (
    echo .env already exists, skipping
)

echo Initializing database...
python scripts\migrate_db.py

echo.
echo ============================================
echo  Setup complete!
echo.
echo  Next steps:
echo  1. Edit .env with your API keys and credentials
echo  2. Drop your resume PDF in data\resumes\
echo  3. Run: venv\Scripts\python.exe main.py
echo     (or activate venv first: venv\Scripts\activate, then: python main.py)
echo  4. Open: http://localhost:5000
echo ============================================
