#!/bin/bash
# Convenience script to run complete_microrunqc_test.py with virtual environment

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Change to the project directory
cd "$SCRIPT_DIR"

# Check if virtual environment exists
if [ ! -d ".venv" ]; then
    echo "❌ Virtual environment not found at $SCRIPT_DIR/.venv"
    echo "Please run: python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

# Load GALAXY_API_KEY / GALAXY_URL from .env if present
if [ -f ".env" ]; then
    set -a
    . ./.env
    set +a
fi

# Activate virtual environment and run the script
echo "🐍 Activating virtual environment..."
source .venv/bin/activate

echo "🚀 Running MicrorunQC test framework..."
python complete_microrunqc_test.py "$@"