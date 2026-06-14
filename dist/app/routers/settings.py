from fastapi import APIRouter, HTTPException
import json
import os
from ..config import settings_file
from ..models import AppSettings, SettingsUpdate
from ..utils import safe_save_json

router = APIRouter()

@router.get("/api/settings")
async def get_settings():
    try:
        if os.path.exists(settings_file):
            with open(settings_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                
                # SURGICAL FIX: Auto-patch old save files. 
                # If the user has an old settings.json from before LavaSR, 
                # we inject the variable so the UI toggle initializes correctly.
                if "use_upscaler" not in data:
                    data["use_upscaler"] = False
                    
                return data
        else:
            return {}
    except Exception as e:
        print(f"[Settings] Error reading settings: {e}")
        return {}


@router.post("/api/settings")
async def save_settings(settings: SettingsUpdate):
    try:
        # 1. Read existing settings first
        current_settings = {}
        if os.path.exists(settings_file):
            with open(settings_file, "r", encoding="utf-8") as f:
                try:
                    current_settings = json.load(f)
                except json.JSONDecodeError:
                    pass
        
        # 2. SURGICAL FIX: Smart Merge
        # We use SettingsUpdate instead of AppSettings so the UI can send 
        # partial updates (like just flipping the Upscaler toggle) without 
        # triggering a Pydantic validation crash for missing fields.
        update_data = settings.model_dump(exclude_unset=True, exclude_none=True)
        current_settings.update(update_data)
        
        # 3. Save the safely merged settings
        safe_save_json(settings_file, current_settings)
        
        return {"status": "ok", "updated_keys": list(update_data.keys())}
        
    except Exception as e:
        print(f"[Settings] Error saving settings: {e}")
        raise HTTPException(status_code=500, detail="Failed to save settings")