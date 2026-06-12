from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from pathlib import Path
import shutil
import numpy as np
import soundfile as sf
import asyncio
import traceback
from ..config import base_dir

router = APIRouter()

@router.post("/api/f5/clone")
async def clone_f5_voice(
    name: str = Form(...),
    text: str = Form(...),
    file: UploadFile = File(...)
):
    import app.state as state_module
    
    # 1. Clean and Validate
    folder_name = name.strip().lower().replace(" ", "_")
    if not folder_name:
        raise HTTPException(status_code=400, detail="Voice name cannot be empty.")
        
    voice_dir = base_dir / "voices" / "f5" / folder_name
    voice_dir.mkdir(parents=True, exist_ok=True)
    
    # 2. Save Reference Audio and Transcript
    ref_audio_path = voice_dir / "ref.wav"
    with open(ref_audio_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    ref_text_path = voice_dir / "ref.txt"
    with open(ref_text_path, "w", encoding="utf-8") as f:
        f.write(text.strip())
        
    # 3. Auto-Generate Sample Audio
    f5_model = getattr(state_module, 'f5_model', None)
    sample_path = voice_dir / "sample.wav"
    
    if f5_model is not None:
        print(f"[F5 STUDIO] Generating auto-sample for '{folder_name}'...")
        # A dynamic text string to test the new clone
        sample_text = f"Hello, my name is {name}. This is a sample of my cloned voice. I am ready to read your documents."
        try:
            def run_infer():
                return f5_model.infer(
                    ref_file=str(ref_audio_path),
                    ref_text=text.strip(),
                    gen_text=sample_text,
                    speed=1.0
                )
                
            # Offload generation to background thread so server doesn't freeze
            result = await asyncio.to_thread(run_infer)
            
            # Safe Unpacking (F5 API returns different sizes depending on version)
            if isinstance(result, tuple):
                wav = result[0]
                sr = result[1] if len(result) >= 2 else 24000
            else:
                wav = result
                sr = 24000
                
            # Convert to pure Numpy 1D Array
            if not isinstance(wav, np.ndarray):
                import torch
                if isinstance(wav, torch.Tensor):
                    wav = wav.cpu().numpy()
                else:
                    wav = np.array(wav)
            if wav.ndim > 1:
                wav = wav.flatten()
                
            # Write out the sample.wav
            sf.write(str(sample_path), wav.astype(np.float32), sr, format="WAV", subtype="PCM_16")
            print(f"[F5 STUDIO] Sample generated successfully!")
            
        except Exception as e:
            traceback.print_exc()
            print(f"[F5 STUDIO ERROR] Failed to generate sample: {e}")
            # We don't fail the HTTP request just because the sample failed.
    else:
        print(f"[F5 STUDIO WARNING] Model not loaded in VRAM. Skipping auto-sample generation.")
    
    return {
        "status": "success", 
        "id": folder_name,
        "has_sample": sample_path.exists()
    }

@router.get("/api/f5/voices")
async def get_f5_voices():
    """Returns a clean list of F5 voices tailored for the UI Studio"""
    f5_voices_dir = base_dir / "voices" / "f5"
    f5_voices_dir.mkdir(parents=True, exist_ok=True)
    
    voices = []
    for item in f5_voices_dir.iterdir():
        if item.is_dir() and (item / "ref.wav").exists():
            has_sample = (item / "sample.wav").exists()
            voices.append({
                "id": item.name,
                "name": item.name.replace("_", " ").title(),
                "has_sample": has_sample
            })
            
    # Sort alphabetically
    return {"voices": sorted(voices, key=lambda x: x["name"])}

@router.get("/api/f5/sample/{voice_id}")
async def get_f5_sample(voice_id: str):
    """Serves the auto-generated sample for the UI to play"""
    sample_path = base_dir / "voices" / "f5" / voice_id / "sample.wav"
    
    # If the user restarted the app and the sample doesn't exist, fallback to playing their reference audio
    if not sample_path.exists():
        sample_path = base_dir / "voices" / "f5" / voice_id / "ref.wav"
        
    if sample_path.exists():
        return FileResponse(str(sample_path), media_type="audio/wav")
        
    raise HTTPException(status_code=404, detail="Sample audio not found.")