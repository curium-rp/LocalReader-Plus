from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import json
from pathlib import Path
from ..config import userdata_dir
from ..utils import safe_save_json

router = APIRouter()

# ── Built-in Kokoro 1.0 Presets ───────────────────────────────────────────────

BUILTIN_PRESETS: Dict[str, Dict[str, Any]] = {
    "preset_bella_sarah": {
        "label": "Bella 30 + Sarah 20",
        "slots": [
            {"active": True, "voice": "af_bella", "weight": 0.30},
            {"active": True, "voice": "af_sarah", "weight": 0.20},
        ],
    },
    "preset_heart_bella": {
        "label": "Heart 65 + Bella 35",
        "slots": [
            {"active": True, "voice": "af_heart", "weight": 0.65},
            {"active": True, "voice": "af_bella", "weight": 0.35},
        ],
    },
    "preset_quartet": {
        "label": "Heart 40 + Bella 25 + Nicole 20 + Emma 15",
        "slots": [
            {"active": True, "voice": "af_heart", "weight": 0.40},
            {"active": True, "voice": "af_bella", "weight": 0.25},
            {"active": True, "voice": "af_nicole", "weight": 0.20},
            {"active": True, "voice": "bf_emma", "weight": 0.15},
        ],
    },
    "preset_narrator": {
        "label": "Heart 70 + Fenrir 30",
        "slots": [
            {"active": True, "voice": "af_heart", "weight": 0.70},
            {"active": True, "voice": "am_fenrir", "weight": 0.30},
        ],
    },
    "preset_transatlantic": {
        "label": "Heart 45 + Emma 35 + George 20",
        "slots": [
            {"active": True, "voice": "af_heart", "weight": 0.45},
            {"active": True, "voice": "bf_emma", "weight": 0.35},
            {"active": True, "voice": "bm_george", "weight": 0.20},
        ],
    },
    "preset_authoritative": {
        "label": "Fenrir 50 + Michael 35 + Heart 15",
        "slots": [
            {"active": True, "voice": "am_fenrir", "weight": 0.50},
            {"active": True, "voice": "am_michael", "weight": 0.35},
            {"active": True, "voice": "af_heart", "weight": 0.15},
        ],
    },
}

# ── Storage helpers ───────────────────────────────────────────────────────────

BLEEDING_DIR = userdata_dir / "bleeding"


def _profile_path(name: str) -> Path:
    """Resolve a profile key to a json path inside userdata/bleeding/."""
    safe = "".join(c for c in name if c.isalnum() or c in ("_", "-"))
    if not safe:
        safe = "bleeding"
    return BLEEDING_DIR / f"{safe}.json"


def _init_builtin_presets():
    """Ensure all built-in Kokoro 1.0 presets exist in userdata/bleeding/."""
    for key, defn in BUILTIN_PRESETS.items():
        path = _profile_path(key)
        if not path.exists():
            slots = [dict(s) for s in defn["slots"]]
            while len(slots) < 10:
                slots.append({"active": False, "voice": "", "weight": 0.0})
            payload = {
                "enabled": False,
                "is_preset": True,
                "label": defn["label"],
                "slots": slots,
            }
            safe_save_json(path, payload, indent=2)


def _ensure_dir():
    BLEEDING_DIR.mkdir(parents=True, exist_ok=True)
    _init_builtin_presets()


def _list_profiles() -> List[str]:
    _ensure_dir()
    all_files = {f.stem for f in BLEEDING_DIR.glob("*.json")}
    
    # Exclude internal active state "bleeding" from profile listings
    all_files.discard("bleeding")

    profiles = []
    # 1. Built-in presets in predefined order
    for p_key in BUILTIN_PRESETS:
        if p_key in all_files:
            profiles.append(p_key)
            all_files.remove(p_key)

    # 2. Custom user profiles in alphabetical order
    for remaining in sorted(all_files):
        profiles.append(remaining)

    return profiles if profiles else list(BUILTIN_PRESETS.keys())


# ── Models ────────────────────────────────────────────────────────────────────

class BlendSlot(BaseModel):
    active: bool = False
    voice: str = ""
    weight: float = 0.00


class BlendProfile(BaseModel):
    enabled: bool = False
    slots: List[BlendSlot] = []
    profile: Optional[str] = "bleeding"
    label: Optional[str] = None
    is_preset: Optional[bool] = False


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/api/blending/profiles")
async def get_profiles():
    _ensure_dir()
    keys = _list_profiles()
    result = []
    for key in keys:
        path = _profile_path(key)
        label = None
        is_preset = (key in BUILTIN_PRESETS)
        slots = []
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                label = data.get("label")
                if data.get("is_preset") is not None:
                    is_preset = bool(data.get("is_preset"))
                slots = data.get("slots", [])
            except Exception:
                pass
        result.append({
            "key": key,
            "label": label or key,
            "is_preset": is_preset,
            "slots": slots
        })
    return {"profiles": result}


@router.delete("/api/blending/profiles")
async def delete_profile(profile: str = Query(...)):
    if profile == "bleeding":
        return JSONResponse(status_code=400, content={"error": "Cannot delete active state"})
    if profile in BUILTIN_PRESETS or profile.startswith("preset_"):
        return JSONResponse(status_code=400, content={"error": "Cannot delete built-in preset"})
    path = _profile_path(profile)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("is_preset"):
                return JSONResponse(status_code=400, content={"error": "Cannot delete built-in preset"})
        except Exception:
            pass
        path.unlink()
    return {"status": "ok"}


@router.get("/api/blending/load")
async def load_profile(profile: str = Query("bleeding")):
    _ensure_dir()
    path = _profile_path(profile)
    if not path.exists():
        return {"enabled": False, "slots": []}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@router.post("/api/blending/save")
async def save_profile(data: BlendProfile):
    _ensure_dir()
    name = data.profile or "bleeding"
    if name in BUILTIN_PRESETS or name.startswith("preset_"):
        return JSONResponse(status_code=400, content={"error": "Cannot overwrite built-in preset"})
    path = _profile_path(name)
    payload = {
        "enabled": data.enabled,
        "slots": [s.model_dump() for s in data.slots],
    }
    if data.label:
        payload["label"] = data.label
    safe_save_json(path, payload, indent=2)
    return {"status": "ok", "profile": name}


@router.post("/api/blending/apply")
async def apply_blend(data: dict):
    """
    Receive the blend expression and enabled flag.
    Persist to bleeding.json (active state) and expose to TTS state.
    """
    _ensure_dir()
    path = _profile_path("bleeding")
    try:
        with open(path, "r", encoding="utf-8") as f:
            current = json.load(f)
    except Exception:
        current = {"slots": []}

    current["enabled"] = bool(data.get("enabled", False))
    current["expression"] = data.get("expression", "")
    safe_save_json(path, current, indent=2)

    # Push to live state so TTS router can read it
    try:
        import app.state as state_module
        state_module.blend_expression = data.get("expression", "") if data.get("enabled") else ""
    except Exception:
        pass

    return {"status": "ok", "expression": data.get("expression", "")}
