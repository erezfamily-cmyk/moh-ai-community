"""Run from a repository checkout: python start.py (no pip install required)."""
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent/'src'))
from moh_ai.cli import main

raise SystemExit(main(['serve',*sys.argv[1:]]))
