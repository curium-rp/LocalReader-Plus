import asyncio
import traceback
from fastapi import APIRouter, HTTPException, BackgroundTasks, UploadFile, File, Form
import shutil
from ..config import settings_file, base_dir
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
    ph_cache = {}
    def get_ph(t):
        t_strip = t.strip()
        if not t_strip: return 0
        if t_strip not in ph_cache:
            ph_cache[t_strip] = estimate_phonemes(t_strip)
        return ph_cache[t_strip]

    paragraphs = text.strip().split('\n')
    final_chunks = []

    split_patterns = [
        r'(?<=\.)\s+',                                
        r'(?<=,)\s+',                                 
        r'(?<=[!?;:\(\)\-–—])\s+',                    
        r'\s+(?=\b(?:and|but|or|because|however|therefore|although|which|that|if|when|where|who)\b)', 
        r'\s+'                                        
    ]
    
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
            
        if get_ph(para) <= soft_limit:
            final_chunks.append(para)
            continue
            
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

def synthesize_with_pauses(text: str, voice: str, speed: float, pause_settings: Dict[str, int]):
    import app.state as state_module

    lang = get_language_from_voice(voice)
    segments = re.split(r"([,\.!\?:;。，！？：；、]+|\n)", text)
    sample_rate = SAMPLE_RATE
    plan = []
    last_was_punctuation = False

    char_map = {
        ",": "comma", "，": "comma", "、": "comma", ".": "period", "。": "period",
        "?": "question", "？": "question", "!": "exclamation", "！": "exclamation",
        ":": "colon", "：": "colon", ";": "semicolon", "；": "semicolon",
    }

    for i, segment in enumerate(segments):
        clean_segment = segment.strip()
        if segment == "\n":
            speed_map = [0.50, 0.75, 1.00, 1.20, 1.35, 1.50, 1.75, 2.00, 2.50, 3.00]
            pause_map = [800,  550,  400,  320,  100,  85,   70,   50,   35,   25]
            dynamic_newline_ms = int(np.interp(speed, speed_map, pause_map))
            plan.append({"type": "silence", "ms": dynamic_newline_ms})
            last_was_punctuation = False
            continue

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
            if re.search(r"[a-zA-Z0-9\u3000-\u303f\u3040-\u309f\u30a0-\u30ff\uff00-\uff9f\u4e00-\u9faf\u3400-\u4dbf]", clean_segment):
                sub_chunks = graceful_chunk_for_tts(clean_segment)
                for sc_idx, sub_chunk in enumerate(sub_chunks):
                    plan.append({"type": "tts", "text": sub_chunk, "index": f"{i}_{sc_idx}"})
                last_was_punctuation = False

    tts_tasks = [p for p in plan if p["type"] == "tts"]
    audio_map = {}

    if tts_tasks and state_module.kokoro:
        for t in tts_tasks:
            idx = t["index"]
            try:
                samples, _ = state_module.kokoro.create(t["text"], voice=voice, speed=speed, lang=lang)
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

# --- Update this function in tts.py ---
def generate_cache_key(text, voice, speed, pause_settings, rules, ignore_list, engine):
    lang = get_language_from_voice(voice)
    cache_data = {
        "text": text, "voice": voice, "language": lang, "speed": speed,
        "pause_settings": pause_settings, "rules": [str(r) for r in rules],
        "ignore_list": sorted(ignore_list),
        "engine": engine,  # <-- SURGICAL FIX: Isolates the cache by engine
    }
    cache_string = json.dumps(cache_data, sort_keys=True)
    return hashlib.md5(cache_string.encode("utf-8")).hexdigest()

# --- API Endpoints ---
@router.get("/api/voices/available")
async def get_voices(engine: str = None):
    import app.state as state_module
    import json
    
    # 1. STRICT ENGINE ISOLATION
    active_engine = engine
    if not active_engine:
        active_engine = "kokoro"
        try:
            if settings_file.exists():
                with open(settings_file, "r", encoding="utf-8") as f:
                    settings = json.load(f)
                    active_engine = settings.get("active_engine", "kokoro")
        except Exception:
            pass

    categories = {}

    # 2. FISH-TTS CLONED VOICES ONLY
    if active_engine == "fish":
        fish_voices_dir = base_dir / "voices" / "fish"
        fish_voices_dir.mkdir(parents=True, exist_ok=True)
        
        voices = []
        for item in fish_voices_dir.iterdir():
            if item.is_dir() and (item / "ref.wav").exists():
                if item.name != "default": 
                    voices.append({"id": item.name, "name": item.name.replace("_", " ").title()})
                
        if len(voices) == 0:
            voices.append({"id": "default", "name": "Default Unconditioned Voice"})
            
        categories["Fish"] = {"label": "Fish-TTS Voices", "voices": voices}
        return {"categories": categories}

    # 3. F5-TTS CLONED VOICES ONLY
    if active_engine == "f5":
        f5_voices_dir = base_dir / "voices" / "f5"
        f5_voices_dir.mkdir(parents=True, exist_ok=True)
        
        voices = []
        for item in f5_voices_dir.iterdir():
            if item.is_dir() and (item / "ref.wav").exists():
                if item.name != "default": 
                    voices.append({"id": item.name, "name": item.name.replace("_", " ").title()})
                
        if len(voices) == 0:
            voices.append({"id": "default", "name": "Default Voice (Needs Setup)"})
            
        categories["F5"] = {"label": "F5 Cloned Voices", "voices": voices}
        return {"categories": categories}

    # 4. KOKORO PRESET VOICES ONLY
    if not getattr(state_module, 'kokoro', None):
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

            # Filter out raw internal voice variants
            if voice_id.lower().split("_")[-1] in ["alpha", "beta", "omega", "psi"]:
                continue

            lang_code = get_language_from_voice(voice_id)
            label = get_lang_label(lang_code)

            if lang_code not in categories:
                categories[lang_code] = {"label": label, "voices": []}

            categories[lang_code]["voices"].append({"id": voice_id, "name": get_voice_name(voice_id)})

        for code in categories:
            categories[code]["voices"].sort(key=lambda x: x["name"])

        return {"categories": categories}
    except Exception as e:
        return {"categories": {}}

@router.post("/api/voices/clone")
async def clone_voice(
    name: str = Form(...), 
    text: str = Form(...), 
    file: UploadFile = File(...), 
    engine: str = Form("f5") # <-- Accept the engine parameter!
):
    try:
        folder_name = name.strip().lower().replace(" ", "_")
        if not folder_name:
            raise HTTPException(status_code=400, detail="Voice name cannot be empty.")
            
        if not file.filename.lower().endswith('.wav'):
            raise HTTPException(status_code=400, detail="Only .wav files are supported for voice cloning.")

        # SURGICAL FIX: Save to the correct folder!
        voice_dir = base_dir / "voices" / engine / folder_name
        voice_dir.mkdir(parents=True, exist_ok=True)

        audio_path = voice_dir / "ref.wav"
        with open(audio_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

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

async def synthesize_kokoro_logic(text, request, pause_settings, state_module):
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
            return state_module.kokoro.create(text, voice=selected_voice, speed=float(request.speed or 1.0), lang=lang)
        else:
            chunk_audios = []
            sr = 24000
            for chunk in sub_chunks:
                chunk_samples, current_sr = state_module.kokoro.create(chunk, voice=selected_voice, speed=float(request.speed or 1.0), lang=lang)
                chunk_audios.append(chunk_samples.flatten())
                sr = current_sr
            return safe_concat(chunk_audios), sr

async def synthesize_fish_logic(text, request, state_module):
    # SURGICAL FIX: Import the exact schema types required by Fish-TTS
    from fish_speech.utils.schema import ServeTTSRequest, ServeReferenceAudio

    voices_dir = Path(base_dir) / "voices" / "fish"
    voice_folder = voices_dir / request.voice
    
    ref_audio_path = voice_folder / "ref.wav"
    ref_text_path = voice_folder / "ref.txt"

    references = []

    # 1. Properly load Reference Audio into the ServeReferenceAudio Schema
    if ref_audio_path.exists() and ref_text_path.exists():
        try:
            with open(ref_text_path, "r", encoding="utf-8") as f:
                ref_text = f.read().strip()
            with open(ref_audio_path, "rb") as f:
                ref_audio_bytes = f.read()
            
            # Pack it into the specific Fish-TTS schema object
            references.append(ServeReferenceAudio(audio=ref_audio_bytes, text=ref_text))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to read Fish reference files: {str(e)}")
    
    fish_engine = getattr(state_module, 'fish_engine', None)
    if fish_engine is None:
        raise HTTPException(status_code=503, detail="Fish-TTS model is not loaded in memory. Please initialize it.")

    print(f"[FISH-TTS] Generating audio for voice: '{request.voice}' | Text length: {len(text)}")

    try:
        def run_fish_inference():
            # Build the exact request schema matching the API
            req = ServeTTSRequest(
                text=text,
                references=references,  
                chunk_length=400,          # <-- INCREASED from 200 (Allows longer breaths)
                max_new_tokens=2048,       # <-- ADDED: Prevents the AI from cutting off early
                top_p=0.8,                 # <-- Optional: Standard temp for stable generation
                repetition_penalty=1.1,    # <-- Optional: Prevents stuttering
                format="wav",
                normalize=True
            )
            
            audio_chunks = []
            sample_rate = 44100 # Default Fish SR
            
            # 3. Iterate through Fish's generator and collect chunks sequentially
            for result in fish_engine.inference(req):
                if result.code in ["segment", "final"]:
                    if isinstance(result.audio, tuple):
                        sample_rate = result.audio[0]
                        audio_chunks.append(result.audio[1])  # Extract numpy array
                elif result.code == "error":
                    raise Exception(str(result.error))

            # 4. Smart Pause Injection (Tail-end padding)
        # --- UPDATE INSIDE tts.py (synthesize_fish_logic) ---

            # 4. Smart Speed & Pause Injection
            if audio_chunks:
                final_audio = np.concatenate(audio_chunks)
                
                # ==========================================
                # SURGICAL FIX: Post-Process Speed for Fish
                # ==========================================
                target_speed = float(getattr(request, 'speed', 1.0))
                if target_speed != 1.0:
                    try:
                        import librosa
                        # Pitch-preserving time stretch
                        final_audio = librosa.effects.time_stretch(final_audio, rate=target_speed)
                    except ImportError:
                        print("[FISH-TTS WARNING] 'librosa' is not installed. Cannot adjust speed. Run: pip install librosa")
                
                # Fetch pause settings from the frontend request
                pause_settings = getattr(request, 'pause_settings', {}) or {}
                extra_silence_ms = 0
                
                # Check how the text ends and apply the matching UI pause setting
                if text.endswith('\n'):
                    extra_silence_ms = pause_settings.get("newline", 800)
                elif text.rstrip().endswith('.') or text.rstrip().endswith('。'):
                    extra_silence_ms = pause_settings.get("period", 600)
                elif text.rstrip().endswith('?') or text.rstrip().endswith('？'):
                    extra_silence_ms = pause_settings.get("question", 600)
                elif text.rstrip().endswith('!') or text.rstrip().endswith('！'):
                    extra_silence_ms = pause_settings.get("exclamation", 600)

                # Dynamically append the exact milliseconds of silence
                if extra_silence_ms > 0:
                    # SURGICAL FIX: Shrink the pause gap if reading faster!
                    if target_speed != 1.0:
                        extra_silence_ms = int(extra_silence_ms / target_speed)
                        
                    silence_samples = int((extra_silence_ms / 1000.0) * sample_rate)
                    silence_array = np.zeros(silence_samples, dtype=np.float32)
                    final_audio = np.concatenate([final_audio, silence_array])
                    
                return final_audio, sample_rate
            else:
                return np.zeros(int(44100 * 0.1), dtype=np.float32), 44100
        # Execute the generator loop safely in a thread to unblock FastAPI
        wav, sr = await asyncio.to_thread(run_fish_inference)
        
        # Ensure correct float32 type for soundfile pipeline and caching
        return wav.astype(np.float32), sr

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[FISH-TTS ERROR] Synthesis crashed: {str(e)}")
        return np.zeros(int(44100 * 0.1), dtype=np.float32), 44100


async def synthesize_f5_logic(text, request, state_module):
    voices_dir = Path(base_dir) / "voices" / "f5"
    voice_folder = voices_dir / request.voice
    
    ref_audio_path = voice_folder / "ref.wav"
    ref_text_path = voice_folder / "ref.txt"

    if not ref_audio_path.exists() or not ref_text_path.exists():
        print(f"[F5-TTS] Voice '{request.voice}' missing reference files. Auto-switching to 'default'.")
        voice_folder = voices_dir / "default"
        ref_audio_path = voice_folder / "ref.wav"
        ref_text_path = voice_folder / "ref.txt"
        
        if not ref_audio_path.exists():
             raise HTTPException(status_code=500, detail="F5 default reference audio is missing! Please clone a voice first.")

    try:
        with open(ref_text_path, "r", encoding="utf-8") as f:
            ref_text = f.read().strip()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read reference text: {str(e)}")

    f5_model = getattr(state_module, 'f5_model', None)
    if f5_model is None:
        raise HTTPException(status_code=503, detail="F5-TTS model is not loaded in memory. Please initialize it.")

    print(f"[F5-TTS] Generating audio for voice: '{voice_folder.name}' | Text length: {len(text)}")

    try:
        speed_val = float(request.speed) if hasattr(request, 'speed') and request.speed else 1.0

        def run_f5_inference():
            return f5_model.infer(ref_file=str(ref_audio_path), ref_text=ref_text, gen_text=text, speed=speed_val)

        result = await asyncio.to_thread(run_f5_inference)

        if isinstance(result, tuple):
            if len(result) == 3:
                wav, sr, _ = result 
            elif len(result) == 2:
                wav, sr = result
            else:
                wav = result[0]
                sr = 24000
        else:
            wav = result
            sr = 24000

        if not isinstance(wav, np.ndarray):
            import torch
            if isinstance(wav, torch.Tensor):
                wav = wav.cpu().numpy()
            else:
                wav = np.array(wav)
                
        if wav.ndim > 1:
            wav = wav.flatten()

        return wav.astype(np.float32), sr
    except Exception as e:
        traceback.print_exc()
        print(f"[F5-TTS ERROR] Synthesis crashed: {str(e)}")
        return np.zeros(int(24000 * 0.1), dtype=np.float32), 24000

@router.post("/api/synthesize")
async def synthesize(request: SynthesisRequest):
    import app.state as state_module

    # ==========================================
    # 1. ENGINE CHECK AND ROUTER
    # ==========================================
    has_kokoro = getattr(state_module, 'kokoro', None) is not None
    has_f5 = getattr(state_module, 'f5_model', None) is not None
    has_fish = getattr(state_module, 'fish_engine', None) is not None


    # Safely get engine from request, fallback to kokoro if missing
    actual_engine = getattr(request, 'engine', 'kokoro')
    
    # Smart Fallback logic
    if actual_engine == "kokoro" and not has_kokoro:
        if has_fish: actual_engine = "fish"
        elif has_f5: actual_engine = "f5"
    elif actual_engine == "f5" and not has_f5:
        if has_kokoro: actual_engine = "kokoro"
        elif has_fish: actual_engine = "fish"
    elif actual_engine == "fish" and not has_fish:
        if has_kokoro: actual_engine = "kokoro"
        elif has_f5: actual_engine = "f5"
        
    if not has_kokoro and not has_f5 and not has_fish:
        raise HTTPException(status_code=503, detail="No Engine is loaded in memory. Please Setup Voice Engine.")

    try:
        # ==========================================
        # 2. TEXT PIPELINE & CACHE CHECKING
        # ==========================================
        raw_text = request.text
        if not raw_text or not raw_text.strip():
            return StreamingResponse(
                io.BytesIO(np.zeros(int(24000 * 0.1), dtype=np.float32).tobytes()),
                media_type="audio/wav"
            )

        text = filter_text_for_tts(raw_text, request.ignore_list or [])
        text = apply_custom_pronunciations(text, request.rules or [])
        text = fix_special_formats(text)

        pause_settings = request.pause_settings or {}
        
        # SURGICAL FIX: Pass 'actual_engine' into the cache generator
        cache_key = generate_cache_key(
            text, request.voice, request.speed, pause_settings, 
            request.rules or [], request.ignore_list or [], actual_engine
        )

        cached_audio = audio_cache.get(cache_key)
        if cached_audio:
            return StreamingResponse(
                io.BytesIO(cached_audio),
                media_type="audio/wav",
                headers={"Content-Length": str(len(cached_audio))},
            )


        # 3. ENGINE ROUTING
        samples = None
        sample_rate = 24000

        if actual_engine == "kokoro":
            samples, sample_rate = await synthesize_kokoro_logic(text, request, pause_settings, state_module)
        elif actual_engine == "fish":
            samples, sample_rate = await synthesize_fish_logic(text, request, state_module)
        elif actual_engine == "f5":
            samples, sample_rate = await synthesize_f5_logic(text, request, state_module)

        if samples is None:
            samples = np.zeros(int(24000 * 0.1), dtype=np.float32)

        # ==========================================
        # 4. AUDIO OUTPUT & SAFE CACHING
        # ==========================================
        buffer = io.BytesIO()
        sf.write(buffer, samples.flatten(), sample_rate, format="WAV", subtype="PCM_16")
        audio_bytes = buffer.getvalue()

        try:
            audio_cache.put(cache_key, audio_bytes)
        except Exception as write_err:
            print(f"[CACHE WARNING] Could not write to audio_cache.db: {write_err}")

        return StreamingResponse(
            io.BytesIO(audio_bytes),
            media_type="audio/wav",
            headers={"Content-Length": str(len(audio_bytes))},
        )
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))