from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
import json
from ..config import userdata_dir
from ..utils import safe_save_json

router = APIRouter()
theme_file = userdata_dir / "themes.json"

VALID_HIDE_MODES = {"always", "auto", "manual"}


class ThemeSettings(BaseModel):
    theme_id: Optional[str] = None
    player_hide_mode: Optional[str] = None
    sentence_dim: Optional[bool] = None


def _load_theme():
    data = {"theme_id": "dark"}
    if theme_file.exists():
        try:
            with open(theme_file, "r") as f:
                stored = json.load(f)
            if isinstance(stored, dict):
                data.update(stored)
        except Exception:
            pass
    data["theme_id"] = data.get("theme_id") or "dark"
    data["sentence_dim"] = data.get("sentence_dim") is True
    mode = data.get("player_hide_mode")
    if mode is not None and mode not in VALID_HIDE_MODES:
        data["player_hide_mode"] = "always"
    elif mode is None:
        data.pop("player_hide_mode", None)
    return data


@router.get("/api/theme")
async def get_theme():
    return _load_theme()


@router.post("/api/theme")
async def save_theme(settings: ThemeSettings):
    current = _load_theme()
    incoming = settings.model_dump(exclude_none=True)
    if incoming.get("player_hide_mode") not in VALID_HIDE_MODES:
        incoming.pop("player_hide_mode", None)
    current.update(incoming)
    try:
        safe_save_json(theme_file, current)
        return {"status": "success"}
    except Exception as e:
        return {"error": str(e)}
