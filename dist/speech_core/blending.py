"""Voice blend profiles in one directory, the active blend, and their routes."""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .tts_helpers import is_blend_expression

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

ACTIVE = "bleeding"  # The active state's file; never listed as a profile.


class BlendSlot(BaseModel):
    active: bool = False
    voice: str = ""
    weight: float = 0.00


class BlendProfile(BaseModel):
    enabled: bool = False
    slots: List[BlendSlot] = []
    profile: Optional[str] = ACTIVE
    label: Optional[str] = None
    is_preset: Optional[bool] = False


def _is_builtin(name):
    return name in BUILTIN_PRESETS or name.startswith("preset_")


class Blends:
    """Profiles live as JSON files in `directory`; `save_json(path, data, indent)`
    is the app's atomic writer. `active` is the expression speech uses, "" when off."""

    def __init__(self, directory: Path, save_json):
        self.directory = Path(directory)
        self.save_json = save_json
        self.active = self._read_active()

    @property
    def active_blend(self):
        """The active expression when it is a valid blend, else ""."""
        return self.active if self.active and is_blend_expression(self.active) else ""

    def _read_active(self):
        try:
            data = json.loads(self._path(ACTIVE).read_text(encoding="utf-8"))
            return data.get("expression", "") if data.get("enabled") else ""
        except Exception:
            return ""

    def _path(self, name: str) -> Path:
        safe = "".join(c for c in name if c.isalnum() or c in ("_", "-")) or ACTIVE
        return self.directory / f"{safe}.json"

    def _read(self, path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _ensure(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        for key, definition in BUILTIN_PRESETS.items():
            path = self._path(key)
            if not path.exists():
                slots = [dict(s) for s in definition["slots"]]
                slots += [{"active": False, "voice": "", "weight": 0.0}] * (10 - len(slots))
                self.save_json(path, {"enabled": False, "is_preset": True,
                                      "label": definition["label"], "slots": slots}, indent=2)

    def _names(self) -> List[str]:
        """Built-in presets in their order, then custom profiles alphabetically."""
        self._ensure()
        stems = {f.stem for f in self.directory.glob("*.json")} - {ACTIVE}
        names = [key for key in BUILTIN_PRESETS if key in stems]
        names += sorted(stems - set(names))
        return names or list(BUILTIN_PRESETS)

    def router(self) -> APIRouter:
        router = APIRouter()

        @router.get("/api/blending/profiles")
        async def get_profiles():
            result = []
            for key in self._names():
                path, label, is_preset, slots = self._path(key), None, key in BUILTIN_PRESETS, []
                if path.exists():
                    try:
                        data = self._read(path)
                        label = data.get("label")
                        if data.get("is_preset") is not None:
                            is_preset = bool(data.get("is_preset"))
                        slots = data.get("slots", [])
                    except Exception:
                        pass
                result.append({"key": key, "label": label or key, "is_preset": is_preset, "slots": slots})
            return {"profiles": result}

        @router.delete("/api/blending/profiles")
        async def delete_profile(profile: str = Query(...)):
            if profile == ACTIVE:
                return JSONResponse(status_code=400, content={"error": "Cannot delete active state"})
            if _is_builtin(profile):
                return JSONResponse(status_code=400, content={"error": "Cannot delete built-in preset"})
            path = self._path(profile)
            if path.exists():
                try:
                    if self._read(path).get("is_preset"):
                        return JSONResponse(status_code=400, content={"error": "Cannot delete built-in preset"})
                except Exception:
                    pass
                path.unlink()
            return {"status": "ok"}

        @router.get("/api/blending/load")
        async def load_profile(profile: str = Query(ACTIVE)):
            self._ensure()
            path = self._path(profile)
            return self._read(path) if path.exists() else {"enabled": False, "slots": []}

        @router.post("/api/blending/save")
        async def save_profile(data: BlendProfile):
            self._ensure()
            name = data.profile or ACTIVE
            if _is_builtin(name):
                return JSONResponse(status_code=400, content={"error": "Cannot overwrite built-in preset"})
            payload: Dict[str, Any] = {"enabled": data.enabled, "slots": [s.model_dump() for s in data.slots]}
            if data.label:
                payload["label"] = data.label
            self.save_json(self._path(name), payload, indent=2)
            return {"status": "ok", "profile": name}

        @router.post("/api/blending/apply")
        async def apply_blend(data: dict):
            """Persist the enabled flag and expression as the active state, and make
            it the blend speech uses."""
            self._ensure()
            path = self._path(ACTIVE)
            try:
                current = self._read(path)
            except Exception:
                current = {"slots": []}
            current["enabled"] = bool(data.get("enabled", False))
            current["expression"] = data.get("expression", "")
            self.save_json(path, current, indent=2)
            self.active = current["expression"] if current["enabled"] else ""
            return {"status": "ok", "expression": current["expression"]}

        return router
