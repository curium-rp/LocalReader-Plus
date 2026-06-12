import shutil
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pathlib import Path
from ..config import base_dir

router = APIRouter()

@router.post("/api/fish/voice/add")
async def add_fish_voice(name: str = Form(...), text: str = Form(...), file: UploadFile = File(...)):
    try:
        folder_name = name.strip().lower().replace(" ", "_")
        if not folder_name:
            raise HTTPException(status_code=400, detail="Voice name cannot be empty.")
            
        if not file.filename.lower().endswith(('.wav', '.mp3', '.flac', '.ogg')):
            raise HTTPException(status_code=400, detail="Only audio files are supported for voice reference.")

        # Save to the fish voices directory
        voice_dir = base_dir / "voices" / "fish" / folder_name
        voice_dir.mkdir(parents=True, exist_ok=True)

        audio_path = voice_dir / "ref.wav"
        with open(audio_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        text_path = voice_dir / "ref.txt"
        with open(text_path, "w", encoding="utf-8") as f:
            f.write(text.strip())

        return {"status": "success", "message": f"Fish voice '{name}' added successfully!", "id": folder_name}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))