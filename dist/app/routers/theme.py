from fastapi import APIRouter
from pydantic import BaseModel
import json
from ..config import userdata_dir

router = APIRouter()
theme_file = userdata_dir / "themes.json"

class ThemeSettings(BaseModel):
    theme_id: str

@router.get("/api/theme")
async def get_theme():
    # Load the theme from the backend file when the app opens
    if theme_file.exists():
        try:
            with open(theme_file, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"theme_id": "dark"}

@router.post("/api/theme")
async def save_theme(settings: ThemeSettings):
    # Safely save the theme to userdata/themes.json every time you click
    try:
        with open(theme_file, "w") as f:
            json.dump(settings.model_dump(), f)
        return {"status": "success"}
    except Exception as e:
        return {"error": str(e)}