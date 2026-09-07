"""Stable homes for personal guidance and operational data."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / "profile"
STATE = ROOT / "state"
LEGACY = ROOT / "private"
