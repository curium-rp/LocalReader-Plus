from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional, List
import json
from ..config import settings_file, rules_file, ignore_file, state_file
from ..models import AppSettings, PronunciationRule
from ..utils import safe_save_json

router = APIRouter()


# --- App Settings ---
@router.get("/api/settings")
async def get_settings():
    with open(settings_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    # TODO(deprecate): [BACKWARD COMPATIBILITY] Safe to remove in ~6 months.
    # In case legacy callers or older clients expect pronunciationRules inside settings response
    if "pronunciationRules" not in data and rules_file.exists():
        try:
            with open(rules_file, "r", encoding="utf-8") as rf:
                data["pronunciationRules"] = json.load(rf)
        except Exception:
            data["pronunciationRules"] = []
    # TODO(deprecate): [BACKWARD COMPATIBILITY] Safe to remove in ~6 months.
    # In case legacy callers or older clients expect ignoreList inside settings response
    if "ignoreList" not in data and ignore_file.exists():
        try:
            with open(ignore_file, "r", encoding="utf-8") as inf:
                data["ignoreList"] = json.load(inf)
        except Exception:
            data["ignoreList"] = []
    return data


@router.post("/api/settings")
async def save_settings(settings: AppSettings):
    # TODO(deprecate): [BACKWARD COMPATIBILITY] Safe to remove in ~6 months.
    # If legacy client still sends pronunciationRules in settings payload, persist to rules_file
    if settings.pronunciationRules is not None:
        legacy_ordered = [
            {
                "id": r.id,
                "original": r.original,
                "replacement": r.replacement,
                "match_case": r.match_case,
                "word_boundary": r.word_boundary,
                "is_regex": r.is_regex or False,
            }
            for r in settings.pronunciationRules
        ]
        safe_save_json(rules_file, legacy_ordered, indent=2)

    # TODO(deprecate): [BACKWARD COMPATIBILITY] Safe to remove in ~6 months.
    # If legacy client still sends ignoreList in settings payload, persist to ignore_file
    if settings.ignoreList is not None:
        safe_save_json(ignore_file, settings.ignoreList, indent=2)

    clean_settings = settings.model_dump(exclude_none=True)
    clean_settings.pop("pronunciationRules", None)
    clean_settings.pop("ignoreList", None)
    safe_save_json(settings_file, clean_settings, indent=2)
    return {"status": "ok"}


# --- Pronunciation Rules ---
@router.get("/api/rules")
async def get_rules():
    if not rules_file.exists():
        return []
    try:
        with open(rules_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


@router.post("/api/rules")
async def save_rules(rules: List[PronunciationRule]):
    # Preserve user order and ensure standardized field arrangement in storage
    ordered_rules = [
        {
            "id": r.id,
            "original": r.original,
            "replacement": r.replacement,
            "match_case": r.match_case,
            "word_boundary": r.word_boundary,
            "is_regex": r.is_regex or False,
        }
        for r in rules
    ]
    safe_save_json(rules_file, ordered_rules, indent=2)
    return {"status": "ok"}


# --- Ignore List ---
@router.get("/api/ignore")
async def get_ignore():
    if not ignore_file.exists():
        return []
    try:
        with open(ignore_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


@router.post("/api/ignore")
async def save_ignore(ignore_list: List[str]):
    safe_save_json(ignore_file, ignore_list, indent=2)
    return {"status": "ok"}


# --- App State Settings (Wake Lock & Ephemeral UI State) ---
VALID_WAKE_LOCK_MODES = {"auto", "on", "off"}


class StateSettings(BaseModel):
    wake_lock_mode: Optional[str] = "auto"


def _load_state():
    data = {"wake_lock_mode": "auto"}
    if state_file.exists():
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                stored = json.load(f)
            if isinstance(stored, dict):
                data.update(stored)
        except Exception:
            pass
    if data.get("wake_lock_mode") not in VALID_WAKE_LOCK_MODES:
        data["wake_lock_mode"] = "auto"
    return data


@router.get("/api/state")
async def get_state():
    return _load_state()


@router.post("/api/state")
async def save_state(settings: StateSettings):
    current = _load_state()
    incoming = settings.model_dump(exclude_none=True)
    if incoming.get("wake_lock_mode") not in VALID_WAKE_LOCK_MODES:
        incoming.pop("wake_lock_mode", None)
    current.update(incoming)
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        safe_save_json(state_file, current)
        return {"status": "success", **current}
    except Exception as e:
        return {"error": str(e)}
