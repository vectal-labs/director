"""Validated personal behavior settings. Reading settings never changes files."""
import copy
import json
import math
from pathlib import Path

try:
    from .storage import PROFILE
except ImportError:
    from storage import PROFILE

DEFAULTS = {
    "recheck_seconds": 3600,
    "recent_user_seconds": 180,
    "spot_check_chance": 0.0,
    "legacy_override_decisions": {},
}


def validate(config):
    if not isinstance(config, dict) or set(config) - DEFAULTS.keys():
        raise ValueError("settings must contain only the documented preference fields")
    result = {**copy.deepcopy(DEFAULTS), **config}
    for field in ("recheck_seconds", "recent_user_seconds", "spot_check_chance"):
        value = result[field]
        try:
            valid = type(value) in (int, float) and math.isfinite(value) and value >= 0
        except OverflowError:
            valid = False
        if not valid:
            raise ValueError(f"settings {field} must be a finite number greater than or equal to zero")
    if result["spot_check_chance"] > 1:
        raise ValueError("settings spot_check_chance must be between zero and one")
    decisions = result["legacy_override_decisions"]
    if not isinstance(decisions, dict) or any(
            not isinstance(key, str) or not key.strip()
            or value not in ("unblock", "leave", "wait_for_david", "deny")
            for key, value in decisions.items()):
        raise ValueError("settings legacy_override_decisions must map rule IDs to decisions")
    return result


def read(path=None):
    path = Path(path) if path is not None else PROFILE / "settings.json"
    try:
        return validate(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return copy.deepcopy(DEFAULTS)
    except (OSError, ValueError) as error:
        raise ValueError(f"cannot read settings file {path}: {error}") from error
