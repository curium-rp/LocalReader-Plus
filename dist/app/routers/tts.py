import os
import sys
import io
import re
import hashlib
import json
import asyncio
import numpy as np
import soundfile as sf
import traceback
from typing import Dict
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

# Add app logic to path for imports
base_dir_parent = Path(__file__).parent.parent
if str(base_dir_parent) not in sys.path:
    sys.path.append(str(base_dir_parent))

# Safe relative import to connect to the Omni-Scanner logic
from ..logic.upscaler import apply_upscale

try:
    from logic.smart_content_detector import filter_text_for_tts
    from logic.text_normalizer import apply_custom_pronunciations, fix_special_formats
    from logic.syllable import estimate_phonemes
except ImportError:
    sys.path.append(str(base_dir_parent / "logic"))
    from smart_content_detector import filter_text_for_tts
    from text_normalizer import apply_custom_pronunciations, fix_special_formats
    from syllable import estimate_phonemes

from ..state import audio_cache, kokoro
from ..models import SynthesisRequest
from ..utils import get_language_from_voice
from ..config import base_dir
from kokoro_onnx import SAMPLE_RATE

router = APIRouter()

# ==========================================
# PLAN 3: THE INTERCEPT PIPELINE
# ==========================================
intercept_pipeline: Dict[str, asyncio.Future] = {}

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
    char_map = {
        ",": "comma", "，": "comma", "、": "comma",
        ".": "period", "。": "period",
        "?": "question", "？": "question",
        "!": "exclamation", "！": "exclamation",
        ":": "colon", "：": "colon",
        ";": "semicolon", "；": "semicolon",
    }

    for i, segment in enumerate(segments):
        clean_segment = segment.strip()
        if segment == "\n":
            speed_map = [0.50, 0.75, 1.00, 1.20, 1.35, 1.50, 1.75, 2.00, 2.50, 3.00]
            pause_map = [800,  550,  400,  320,  100,  85,   70,   50,   35,   25]
            dynamic_newline_ms = int(np.interp(speed, speed_map, pause_map))
            plan.append({"type": "silence", "ms": dynamic_newline_ms})
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
        else:
            if re.search(r"[a-zA-Z0-9\u3000-\u303f\u3040-\u309f\u30a0-\u30ff\uff00-\uff9f\u4e00-\u9faf\u3400-\u4dbf]", clean_segment):
                sub_chunks = graceful_chunk_for_tts(clean_segment)
                for sc_idx, sub_chunk in enumerate(sub_chunks):
                    plan.append({"type": "tts", "text": sub_chunk, "index": f"{i}_{sc_idx}"})

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

def generate_cache_key(text, voice, speed, pause_settings, rules, ignore_list, use_upscaler):
    lang = get_language_from_voice(voice)
    cache_data = {
        "text": text,
        "voice": voice,
        "language": lang,
        "speed": speed,
        "pause_settings": pause_settings,
        "rules": [str(r) for r in rules],
        "ignore_list": sorted(ignore_list),
        "upscale": use_upscaler
    }
    cache_string = json.dumps(cache_data, sort_keys=True)
    return hashlib.md5(cache_string.encode("utf-8")).hexdigest()

_upscaler_logged = False

def execute_generation_pipeline(text: str, selected_voice: str, speed: float, pause_settings: dict, upscale_requested: bool) -> bytes:
    global _upscaler_logged
    import app.state as state_module
    lang = get_language_from_voice(selected_voice)
    has_punctuation = any(p in text for p in [",", ".", "!", "?", ":", ";", "\n", "。", "，", "！", "？", "：", "；", "、"])

    # 1. Base Generation (Kokoro)
    if not re.search(r"[a-zA-Z0-9\u3000-\u303f\u3040-\u309f\u30a0-\u30ff\uff00-\uff9f\u4e00-\u9faf\u3400-\u4dbf]", text):
        samples = np.zeros(int(24000 * 0.1), dtype=np.float32)
        sample_rate = 24000
    else:
        if pause_settings and has_punctuation:
            samples, sample_rate = synthesize_with_pauses(text, selected_voice, float(speed), pause_settings)
        else:
            sub_chunks = graceful_chunk_for_tts(text)
            if len(sub_chunks) == 1:
                samples, sample_rate = state_module.kokoro.create(text, voice=selected_voice, speed=float(speed), lang=lang)
            else:
                chunk_audios = []
                sample_rate = SAMPLE_RATE
                for chunk in sub_chunks:
                    chunk_samples, sr = state_module.kokoro.create(chunk, voice=selected_voice, speed=float(speed), lang=lang)
                    chunk_audios.append(chunk_samples.flatten())
                    sample_rate = sr
                samples = safe_concat(chunk_audios)

    # 2. Upscaler Execution (LavaSR)
    if upscale_requested and len(samples) > 0:
        try:
            if not _upscaler_logged:
                print(f"[TTS] Complete! Active Upscaler running in background process...")
                _upscaler_logged = True
                
            samples, sample_rate = apply_upscale(samples.flatten(), sample_rate)
            
            # Normalization / Anti-Clipping to prevent WebAudio static
            max_val = np.max(np.abs(samples))
            if max_val > 1.0:
                samples = samples / max_val
        except Exception as e:
            print(f"\n[TTS PIPELINE ERROR] Upscaler Routing crashed.")
            print(f"Error details: {e}")
            traceback.print_exc()
            print("[TTS] Continuing with original Kokoro audio to prevent UI freeze...\n")

    # 3. Payload Construction
    buffer = io.BytesIO()
    sf.write(buffer, samples.flatten(), sample_rate, format="WAV", subtype="PCM_16")
    return buffer.getvalue()

@router.get("/api/voices/available")
async def get_voices():
    import app.state as state_module
    if not state_module.kokoro:
        return {"categories": {}}
    try:
        raw_voices = state_module.kokoro.get_voices()
        categories = {}
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
            categories[lang_code]["voices"].append({"id": voice_id, "name": get_voice_name(voice_id)})
        for code in categories:
            categories[code]["voices"].sort(key=lambda x: x["name"])
        return {"categories": categories}
    except Exception as e:
        return {"categories": {}}

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

@router.post("/api/synthesize")
async def synthesize(request: SynthesisRequest):
    import app.state as state_module

    if state_module.kokoro is None:
        raise HTTPException(status_code=503, detail="TTS Engine not initialized.")

    try:
        text = fix_special_formats(request.text)
        text = filter_text_for_tts(text)
        rules_data = [r.model_dump() for r in request.rules]
        text = apply_custom_pronunciations(text, rules_data, request.ignore_list)
    except Exception:
        text = fix_special_formats(request.text)
        text = filter_text_for_tts(text)

    voices = state_module.kokoro.get_voices()
    selected_voice = request.voice if request.voice in voices else "af_sky"
    pause_settings = request.pause_settings or {}
    
    # Read the explicit Upscale toggle state passed through models.py
    upscale_requested = getattr(request, "use_upscaler", False)
    
    cache_key = generate_cache_key(
        text, selected_voice, float(request.speed or 1.0),
        pause_settings, request.rules, request.ignore_list, upscale_requested
    )

    # 1. CACHE CHECK
    cached_audio = audio_cache.get(cache_key)
    if cached_audio:
        return StreamingResponse(
            io.BytesIO(cached_audio),
            media_type="audio/wav",
            headers={"Content-Length": str(len(cached_audio))},
        )

    # 2. INTERCEPT LIMIT LOOP (Deduplicate overlapping browser preloads)
    if cache_key in intercept_pipeline:
        try:
            audio_bytes = await intercept_pipeline[cache_key]
            return StreamingResponse(
                io.BytesIO(audio_bytes),
                media_type="audio/wav",
                headers={"Content-Length": str(len(audio_bytes))},
            )
        except Exception:
            pass 

    # 3. DISPATCH NEW GENERATION TASK
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    intercept_pipeline[cache_key] = future

    try:
        # Prevent blocking the FastAPI event loop during heavy GPU operations
        audio_bytes = await run_in_threadpool(
            execute_generation_pipeline,
            text, selected_voice, float(request.speed or 1.0), pause_settings, upscale_requested
        )
        
        audio_cache.put(cache_key, audio_bytes)
        future.set_result(audio_bytes)
        
        return StreamingResponse(
            io.BytesIO(audio_bytes),
            media_type="audio/wav",
            headers={"Content-Length": str(len(audio_bytes))},
        )
        
    except Exception as e:
        future.set_exception(e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        intercept_pipeline.pop(cache_key, None)