#!/bin/bash
set -e

echo "============================================"
echo " JobPilot Setup Script (Linux/Mac)"
echo "============================================"

echo "Creating virtual environment..."
python3 -m venv venv

echo "Activating virtual environment..."
source venv/bin/activate

echo "Installing Python dependencies..."
pip install -r requirements.txt

echo "Installing Playwright browser (Chromium)..."
playwright install chromium --with-deps

echo "Creating data directories..."
mkdir -p data/logs data/audio data/resumes

echo "Setting up environment file..."
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "Created .env from .env.example"
else
    echo ".env already exists, skipping"
fi

echo "Initializing database..."
python scripts/migrate_db.py

echo ""
echo "============================================"
echo " Setup complete!"
echo ""
echo " Next steps:"
echo " 1. Edit .env with your API keys and credentials"
echo " 2. Drop your resume PDF in data/resumes/"
echo " 3. Run: python main.py"
echo " 4. Open: http://localhost:5000"
echo "============================================"
