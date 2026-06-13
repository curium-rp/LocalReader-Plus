from fastapi import APIRouter, HTTPException
import shutil
from pathlib import Path
import sys

# Add app to path for imports
base_dir_parent = Path(__file__).parent.parent.parent
if str(base_dir_parent) not in sys.path:
    sys.path.append(str(base_dir_parent))

from app.config import base_dir

router = APIRouter()

@router.get("/api/fish/voices")
async def list_fish_voices():
    """Helper endpoint to list all custom cloned voices for Fish-TTS"""
    voices_dir = base_dir / "voices" / "fish"
    if not voices_dir.exists():
        return {"voices": []}
        
    voices = []
    for item in voices_dir.iterdir():
        if item.is_dir() and (item / "ref.wav").exists():
            voices.append({"id": item.name, "name": item.name.replace("_", " ").title()})
    return {"voices": voices}

@router.delete("/api/fish/voices/{voice_id}")
async def delete_fish_voice(voice_id: str):
    """Allows users to safely delete a cloned voice via API"""
    if voice_id.lower() == "default":
        raise HTTPException(status_code=400, detail="Cannot delete the default voice.")
        
    voice_dir = base_dir / "voices" / "fish" / voice_id
    if voice_dir.exists() and voice_dir.is_dir():
        try:
            shutil.rmtree(voice_dir)
            return {"status": "success", "message": f"Voice '{voice_id}' has been permanently deleted."}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to delete voice: {str(e)}")
    else:
        raise HTTPException(status_code=404, detail=f"Voice '{voice_id}' not found.")