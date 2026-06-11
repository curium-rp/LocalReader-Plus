from fastapi import APIRouter, HTTPException, BackgroundTasks, UploadFile, File, Form
import shutil
from ..config import settings_file
from fastapi.responses import StreamingResponse
import numpy as np
import io
import re
import hashlib
import soundfile as sf
import concurrent.futures
from typing import Dict
import sys
import json
from pathlib import Path

# Add app logic to path for imports
base_dir_parent = Path(__file__).parent.parent
if str(base_dir_parent) not in sys.path:
    sys.path.append(str(base_dir_parent))

try:
    from logic.smart_content_detector import filter_text_for_tts
    from logic.text_normalizer import apply_custom_pronunciations, fix_special_formats
    from logic.syllable import estimate_phonemes  # <-- link syllable 
except ImportError:
    sys.path.append(str(base_dir_parent / "logic"))
    from smart_content_detector import filter_text_for_tts
    from text_normalizer import apply_custom_pronunciations, fix_special_formats
    from syllable import estimate_phonemes  # <-- Link syllable

from ..state import audio_cache, kokoro
from ..models import SynthesisRequest
from ..utils import get_language_from_voice
from ..config import base_dir
from kokoro_onnx import SAMPLE_RATE

router = APIRouter()


# --- Helpers moved from server.py ---
def safe_concat(audio_list):
    clean_list = []
    for a in audio_list:
        if isinstance(a, np.ndarray):
            if a.ndim == 2:
                a = a.squeeze()
            if a.ndim > 2:
                a = a.flatten()
        clean_list.append(a)
    if not clean_list:
        return np.array([], dtype=np.float32)
    return np.concatenate(clean_list)


    # Kororo onnx tts has limit max phonemes=510 use english to ipa to culcalate, Buffer 20 for safety
def graceful_chunk_for_tts(text, soft_limit=450, hard_limit=490):
    # Micro-cache to prevent duplicate calculations with syllable.py
    ph_cache = {}
    def get_ph(t):
        t_strip = t.strip()
        if not t_strip: return 0
        if t_strip not in ph_cache:
            ph_cache[t_strip] = estimate_phonemes(t_strip)
        return ph_cache[t_strip]

    # 1. First cut: Natural newlines
    paragraphs = text.strip().split('\n')
    final_chunks = []

    # The step-by-step fallback cascade (5 Levels)
    split_patterns = [
        r'(?<=\.)\s+',                                # Level 1: Full stops only
        r'(?<=,)\s+',                                 # Level 2: Commas only
        r'(?<=[!?;:\(\)\-–—])\s+',                    # Level 3: Exclamations, questions, colons, brackets, hyphens, and long dashes (—)
        r'\s+(?=\b(?:and|but|or|because|however|therefore|although|which|that|if|when|where|who)\b)', # Level 4: Pause/Breath words
        r'\s+'                                        # Level 5: Spaces between words (Emergency)
    ]
    
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
            
        # ==========================================
        # THE SMART CHECK (The Fast Lane)
        # ==========================================
        if get_ph(para) <= soft_limit:
            final_chunks.append(para)
            continue
            
        # ==========================================
        # THE FALLBACK (The Danger Zone)
        # ==========================================
        pieces_to_process = [para]
        
        for pattern in split_patterns:
            new_pieces = []
            for piece in pieces_to_process:
                if get_ph(piece) <= hard_limit:
                    new_pieces.append(piece) 
                else:
                    sub_pieces = re.split(pattern, piece, flags=re.IGNORECASE)
                    new_pieces.extend([sp.strip() for sp in sub_pieces if sp.strip()])
            
            pieces_to_process = new_pieces
            
            if all(get_ph(p) <= hard_limit for p in pieces_to_process):
                break
                
        current_chunk = ""
        current_ph_count = 0
        
        for piece in pieces_to_process:
            piece_ph = get_ph(piece)
            if current_ph_count + piece_ph + 1 <= soft_limit:
                current_chunk = f"{current_chunk} {piece}".strip()
                current_ph_count += piece_ph
            else:
                if current_chunk:
                    final_chunks.append(current_chunk)
                current_chunk = piece
                current_ph_count = piece_ph
                
        if current_chunk:
            final_chunks.append(current_chunk)
            
    return final_chunks

def synthesize_with_pauses(
    text: str, voice: str, speed: float, pause_settings: Dict[str, int]
):
    import app.state as state_module

    lang = get_language_from_voice(voice)
    segments = re.split(r"([,\.!\?:;。，！？：；、]+|\n)", text)
    sample_rate = SAMPLE_RATE
    plan = []
    last_was_punctuation = False

    char_map = {
        ",": "comma",
        "，": "comma",
        "、": "comma",
        ".": "period",
        "。": "period",
        "?": "question",
        "？": "question",
        "!": "exclamation",
        "！": "exclamation",
        ":": "colon",
        "：": "colon",
        ";": "semicolon",
        "；": "semicolon",
    }

    for i, segment in enumerate(segments):
        clean_segment = segment.strip()
        # --- newline dynamics adjust ---
        if segment == "\n":
            speed_map = [0.50, 0.75, 1.00, 1.20, 1.35, 1.50, 1.75, 2.00, 2.50, 3.00]
            pause_map = [800,  550,  400,  320,  100,  85,   70,   50,   35,   25]
            
            # Use the speed variable passed into the function for the math
            dynamic_newline_ms = int(np.interp(speed, speed_map, pause_map))
            
            # A paragraph break always gets its silence, period or not
            plan.append({"type": "silence", "ms": dynamic_newline_ms})
            
            last_was_punctuation = False
            continue
        # --- END OF NEWLINE HANDLING ---

        if not clean_segment:
            continue

        if re.match(r"^[,\.!\?:;。，！？：；、]+$", clean_segment):
            last_char = clean_segment[-1]
            pause_ms = 0

            vocab_key = char_map.get(last_char)
            if vocab_key:
                pause_ms = pause_settings.get(vocab_key, 100)
            
            plan.append({"type": "silence", "ms": pause_ms})
            last_was_punctuation = True
        else:
            if re.search(
                r"[a-zA-Z0-9\u3000-\u303f\u3040-\u309f\u30a0-\u30ff\uff00-\uff9f\u4e00-\u9faf\u3400-\u4dbf]",
                clean_segment,
            ):
                sub_chunks = graceful_chunk_for_tts(clean_segment)
                for sc_idx, sub_chunk in enumerate(sub_chunks):
                    plan.append(
                        {"type": "tts", "text": sub_chunk, "index": f"{i}_{sc_idx}"}
                    )
                last_was_punctuation = False

    tts_tasks = [p for p in plan if p["type"] == "tts"]
    audio_map = {}

    if tts_tasks and state_module.kokoro:
        for t in tts_tasks:
            idx = t["index"]
            try:
                samples, _ = state_module.kokoro.create(
                    t["text"],
                    voice=voice,
                    speed=speed,
                    lang=lang,
                )
                audio_map[idx] = samples.flatten()
            except Exception as e:
                print(f"Segment {idx} failed: {e}")
                audio_map[idx] = None

    final_segments = []
    for item in plan:
        if item["type"] == "silence":
            pause_samples = int((item["ms"] / 1000.0) * sample_rate)
            if pause_samples > 0:
                final_segments.append(np.zeros(pause_samples, dtype=np.float32))
        elif item["type"] == "tts":
            audio = audio_map.get(item["index"])
            if audio is not None:
                final_segments.append(audio)

    if final_segments:
        return safe_concat(final_segments), sample_rate
    return np.zeros(int(sample_rate * 0.1), dtype=np.float32), sample_rate

def generate_cache_key(text, voice, speed, pause_settings, rules, ignore_list):
    lang = get_language_from_voice(voice)
    cache_data = {
        "text": text,
        "voice": voice,
        "language": lang,
        "speed": speed,
        "pause_settings": pause_settings,
        "rules": [str(r) for r in rules],
        "ignore_list": sorted(ignore_list),
    }
    cache_string = json.dumps(cache_data, sort_keys=True)
    return hashlib.md5(cache_string.encode("utf-8")).hexdigest()

# --- API Endpoints ---

@router.get("/api/voices/available")
async def get_voices():
    import app.state as state_module
    
    # 1. Determine which engine is active
    active_engine = "kokoro"
    try:
        if settings_file.exists():
            with open(settings_file, "r", encoding="utf-8") as f:
                settings = json.load(f)
                active_engine = settings.get("active_engine", "kokoro")
    except Exception:
        pass

    categories = {}

    # ==========================================
    # MARVIS VOICES (Dynamic Folder Scanning)
    # ==========================================
    if active_engine == "marvis":
        marvis_voices_dir = base_dir / "voices" / "marvis"
        marvis_voices_dir.mkdir(parents=True, exist_ok=True)
        
        voices = []
        # Scan the directory for folders that contain a ref.wav
        for item in marvis_voices_dir.iterdir():
            if item.is_dir() and (item / "ref.wav").exists():
                voices.append({"id": item.name, "name": item.name.replace("_", " ").title()})
                
        if not voices:
            voices.append({"id": "default", "name": "Default Marvis Voice"})
            
        categories["en"] = {"label": "Marvis Cloned Voices", "voices": voices}
        return {"categories": categories}

    # ==========================================
    # KOKORO VOICES (Original Logic)
    # ==========================================
    if not state_module.kokoro:
        return {"categories": {}}

    try:
        raw_voices = state_module.kokoro.get_voices()

        def get_voice_name(vid):
            parts = vid.split("_")
            if len(parts) > 1:
                return parts[1].title()
            return vid

        def get_lang_label(code):
            maps = {
                "en-us": "English (US)", "en-gb": "English (UK)", "fr-fr": "French",
                "es": "Spanish", "cmn": "Chinese (Mandarin)", "it": "Italian",
                "pt-br": "Portuguese (Brazil)", "ja": "Japanese",
            }
            return maps.get(code, "Other")

        for voice in raw_voices:
            voice_id = voice if isinstance(voice, str) else voice.get("id")

            if voice_id.lower().split("_")[-1] in ["alpha", "beta", "omega", "psi"]:
                continue

            lang_code = get_language_from_voice(voice_id)
            label = get_lang_label(lang_code)

            if lang_code not in categories:
                categories[lang_code] = {"label": label, "voices": []}

            categories[lang_code]["voices"].append(
                {"id": voice_id, "name": get_voice_name(voice_id)}
            )

        for code in categories:
            categories[code]["voices"].sort(key=lambda x: x["name"])

        return {"categories": categories}

    except Exception as e:
        return {"categories": {}}


# ==========================================
# NEW VOICE CLONING ENDPOINT
# ==========================================
@router.post("/api/voices/clone")
async def clone_voice(
    name: str = Form(...), 
    text: str = Form(...), 
    file: UploadFile = File(...)
):
    """Takes an uploaded .wav and transcript, and creates a Marvis voice folder."""
    try:
        # Clean up the folder name (e.g., "My Voice" -> "my_voice")
        folder_name = name.strip().lower().replace(" ", "_")
        if not folder_name:
            raise HTTPException(status_code=400, detail="Voice name cannot be empty.")
            
        if not file.filename.lower().endswith('.wav'):
            raise HTTPException(status_code=400, detail="Only .wav files are supported for voice cloning.")

        voice_dir = base_dir / "voices" / "marvis" / folder_name
        voice_dir.mkdir(parents=True, exist_ok=True)

        # Save the audio file as ref.wav
        audio_path = voice_dir / "ref.wav"
        with open(audio_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # Save the transcript as ref.txt
        text_path = voice_dir / "ref.txt"
        with open(text_path, "w", encoding="utf-8") as f:
            f.write(text.strip())

        return {"status": "success", "message": f"Voice '{name}' cloned successfully!", "id": folder_name}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/locale/{lang}")
async def get_locale(lang: str):
    locale_dir = base_dir / "locales"
    file_path = locale_dir / f"{lang}.json"
    if not file_path.exists():
        file_path = locale_dir / "en.json"
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

import torch
from pathlib import Path

async def synthesize_kokoro_logic(text, request, pause_settings, state_module):
    """Your original Kokoro TTS logic, isolated for cleanliness."""
    voices = state_module.kokoro.get_voices()
    selected_voice = request.voice if request.voice in voices else "af_sky"
    lang = get_language_from_voice(selected_voice)
    
    punctuation_chars = [",", ".", "!", "?", ":", ";", "\n", "。", "，", "！", "？", "：", "；", "、"]
    has_punctuation = any(p in text for p in punctuation_chars)

    if not re.search(r"[a-zA-Z0-9\u3000-\u30ff\u4e00-\u9faf]", text):
        return np.zeros(int(24000 * 0.1), dtype=np.float32), 24000

    if pause_settings and has_punctuation:
        return synthesize_with_pauses(text, selected_voice, float(request.speed or 1.0), pause_settings)
    else:
        sub_chunks = graceful_chunk_for_tts(text)
        if len(sub_chunks) == 1:
            return state_module.kokoro.create(
                text, voice=selected_voice, speed=float(request.speed or 1.0), lang=lang
            )
        else:
            chunk_audios = []
            sr = 24000
            for chunk in sub_chunks:
                chunk_samples, current_sr = state_module.kokoro.create(
                    chunk, voice=selected_voice, speed=float(request.speed or 1.0), lang=lang
                )
                chunk_audios.append(chunk_samples.flatten())
                sr = current_sr
            return safe_concat(chunk_audios), sr

async def synthesize_marvis_logic(text, request, state_module):
    """New Marvis TTS logic adapted from inference.py"""
    from marvis_tts.utils import Segment
    
    generator = state_module.marvis_generator
    tokenizer = state_module.marvis_tokenizer
    device = generator.device

    # For zero-shot cloning, Marvis requires a reference audio and text.
    # We map the requested 'voice' ID to a folder containing .wav and .txt
    voices_dir = Path(base_dir) / "voices" / "marvis"
    voice_folder = voices_dir / request.voice
    
    ref_audio_path = voice_folder / "ref.wav"
    ref_text_path = voice_folder / "ref.txt"

    # Fallback to a default if the custom voice isn't set up yet
    if not ref_audio_path.exists() or not ref_text_path.exists():
        # Let's create a silent fallback or raise an error. For now, raise.
        raise Exception(f"Marvis Zero-Shot files missing for voice: {request.voice}. Expected {ref_audio_path}")

    # 1. Read Reference Audio & Text
    wav, sr = sf.read(str(ref_audio_path))
    if wav.ndim == 2:
        wav = wav.mean(axis=1)
    ref_audio_tensor = torch.tensor(wav[None, None, :], dtype=torch.float32, device=device)
    ref_audio_tokens = generator._audio_tokenizer.encode(ref_audio_tensor)[-1, :, :]

    with open(ref_text_path, "r", encoding="utf-8") as f:
        ref_text = f.read().strip()

    # 2. Build the Marvis Context (combining reference text with the target text)
    all_text = (ref_text + " " + text.strip()).strip()
    context = [
        Segment(
            text=torch.tensor(tokenizer.encode(f"[0]{all_text}"), dtype=torch.long, device=device),
            audio=torch.tensor(ref_audio_tokens, dtype=torch.long, device=device)[:32, :],
            speaker=0,
        )
    ]

    # 3. Generate Audio
    with torch.inference_mode():
        audio_tensor = generator.generate(
            text="",  # Conditioned entirely via context as per inference.py
            speaker=0,
            context=context,
            max_audio_length_ms=30000, # 30 seconds max limit per chunk
            temperature=0.7,
            topk=50,
            voice_match=True,
        )

    # Convert tensor to numpy array for the frontend
    samples = audio_tensor.cpu().numpy()
    
    # Marvis operates natively at 24000Hz (based on inference.py)
    return samples, 24000

@router.post("/api/synthesize")
async def synthesize(request: SynthesisRequest):
    import app.state as state_module

    # 1. Text Pipeline (Applies to ALL engines)
    try:
        text = fix_special_formats(request.text)
        text = filter_text_for_tts(text)
        rules_data = [r.model_dump() for r in request.rules]
        text = apply_custom_pronunciations(text, rules_data, request.ignore_list)
    except Exception:
        text = fix_special_formats(request.text)
        text = filter_text_for_tts(text)

    # 2. Cache Generation (Applies to ALL engines)
    try:
        pause_settings = request.pause_settings or {}
        cache_key = generate_cache_key(
            text,
            request.voice,
            float(request.speed or 1.0),
            pause_settings,
            request.rules,
            request.ignore_list,
        )
        # Add engine to cache key so Kokoro and Marvis don't mix up audio!
        cache_key = f"{request.engine}_{cache_key}"

        cached_audio = audio_cache.get(cache_key)
        if cached_audio:
            return StreamingResponse(
                io.BytesIO(cached_audio),
                media_type="audio/wav",
                headers={"Content-Length": str(len(cached_audio))},
            )

        # ==========================================
        # 3. ENGINE ROUTING (The Backbone)
        # ==========================================
        samples = None
        sample_rate = 24000

        # --- KOKORO ENGINE ---
        if request.engine == "kokoro":
            if state_module.kokoro is None:
                raise HTTPException(status_code=503, detail="Kokoro Engine not loaded.")
            
            samples, sample_rate = await synthesize_kokoro_logic(
                text, request, pause_settings, state_module
            )

        # --- MARVIS ENGINE ---
        elif request.engine == "marvis":
            if state_module.marvis_generator is None:
                raise HTTPException(status_code=503, detail="Marvis Engine not loaded.")
            
            samples, sample_rate = await synthesize_marvis_logic(
                text, request, state_module
            )
            
        else:
            raise HTTPException(status_code=400, detail=f"Unknown engine: {request.engine}")

        # ==========================================
        # 4. Audio Output & Caching
        # ==========================================
        buffer = io.BytesIO()
        sf.write(buffer, samples.flatten(), sample_rate, format="WAV", subtype="PCM_16")
        audio_bytes = buffer.getvalue()

        audio_cache.put(cache_key, audio_bytes)

        return StreamingResponse(
            io.BytesIO(audio_bytes),
            media_type="audio/wav",
            headers={"Content-Length": str(len(audio_bytes))},
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))