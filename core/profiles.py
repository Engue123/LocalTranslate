"""
User profile persistence.
Saves and loads translation preferences.
"""
import json
from pathlib import Path
from typing import Dict, Any

_PROFILE_PATH = Path.home() / ".localtranslate" / "profile.json"


def load_profile() -> Dict[str, Any]:
    if _PROFILE_PATH.exists():
        try:
            return json.loads(_PROFILE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_profile(data: Dict[str, Any]) -> None:
    try:
        _PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PROFILE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def get_last_settings() -> Dict[str, Any]:
    return load_profile().get("last_settings", {})


def save_last_settings(settings: Dict[str, Any]) -> None:
    profile = load_profile()
    profile["last_settings"] = settings
    save_profile(profile)
