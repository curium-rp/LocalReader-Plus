from fastapi import APIRouter, HTTPException, BackgroundTasks
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
    from logic.syllable import estimate_phonemes
    from logic.language_switcher import smart_polyglot_split  
    from logic.japanese_g2p import pure_japanese_to_romaji
    from logic.chinese_g2p import cleanse_chinese_text 
except ImportError:
    sys.path.append(str(base_dir_parent / "logic"))
    from smart_content_detector import filter_text_for_tts
    from text_normalizer import apply_custom_pronunciations, fix_special_formats
    from syllable import estimate_phonemes
    from language_switcher import smart_polyglot_split  
    # 🌟 FIX 1: Corrected fallback import paths
    from japanese_g2p import pure_japanese_to_romaji
    from chinese_g2p import cleanse_chinese_text 

from ..state import audio_cache, kokoro
from ..models import SynthesisRequest
from ..utils import get_language_from_voice
from ..config import base_dir
from kokoro_onnx import SAMPLE_RATE

router = APIRouter()

# ==========================================
# AUDIO ENGINEERING MASTER FIXES
# ==========================================

def create_anti_skip_silence(duration_ms, sample_rate=24000):
    """Generates mathematically non-zero silence to prevent browser/OS optimization skipping."""
    if duration_ms <= 0:
        return np.array([], dtype=np.float32)
    frames = int((duration_ms / 1000.0) * sample_rate)
    # Inject microscopic noise (-80dB) to defeat pure-silence fast-forwarding
    return np.random.uniform(-1e-4, 1e-4, frames).astype(np.float32)

import threading
import multiprocessing
import hashlib

# ==========================================
# CPU CORE MANAGER & ROBUST CACHE LINKING
# ==========================================
# Count total cores for every 6 that have will reserved more 1 core/thread
# For example has 6 thread will reserved 1 if has 16 will reserved 2 core/thread
_total_cores = multiprocessing.cpu_count()
_reserved_cores = max(1, _total_cores // 6)
_safe_cores = max(1, _total_cores - _reserved_cores)
cpu_semaphore = threading.Semaphore(_safe_cores)

print(f"[Engine] CPU Core Manager active: Utilizing {_safe_cores}/{_total_cores} threads (Reserved: {_reserved_cores}).")

# Thread-safe dictionary for exact audio matches to prevent redundant processing
_robust_link_cache = {}
_cache_lock = threading.Lock()

def generate_locked_audio(kokoro_inst, text, voice, speed, lang, target_len):
    """Safely generates audio using CPU core limits and robust cache linking."""
    
    # 1. Generate unique Link ID for the cache check
    cache_string = f"{text}|{voice}|{speed}|{lang}".encode('utf-8')
    cache_key = hashlib.md5(cache_string).hexdigest()
    
    # 2. Check Link Cache (Robust Read)
    with _cache_lock:
        if cache_key in _robust_link_cache:
            return _robust_link_cache[cache_key]
            
    # 3. Generate using available CPU threads (Semaphore limit)
    try:
        with cpu_semaphore:
            audio_data = kokoro_inst.create(text, voice, speed, lang)
            
        # 4. Save to Link Cache (Robust Write)
        if audio_data is not None and len(audio_data[0].flatten()) > 0:
            with _cache_lock:
                # Prevent memory overflow by capping cache size at 500 links
                if len(_robust_link_cache) > 500:
                    _robust_link_cache.clear()
                _robust_link_cache[cache_key] = audio_data
                
        return audio_data
        
    except Exception as e:
        print(f"[Engine] Audio generation failed on CPU thread: {e}. Bypassing with silence.")
        return create_anti_skip_silence(500, 24000), 24000

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
        r'(?<=[.!?。！？])\s*',                                
        r'(?<=[,，、])\s*',                                 
        r'(?<=[;:；：\(\)\-–—])\s*',                    
        r'\s+(?=\b(?:and|but|or|because|however|therefore|although|which|that|if|when|where|who)\b)', 
        r'\s+'                                        
    ]
    
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
            
        if get_ph(para) <= soft_limit:
            final_chunks.append({"text": para, "is_cut": False})
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
                    is_cut = False
                    if not re.search(r'[.,!?;:。，！？：；、\-\—"\'”\)]$', current_chunk):
                        current_chunk += ","
                        is_cut = True
                    final_chunks.append({"text": current_chunk, "is_cut": is_cut})
                current_chunk = piece
                current_ph_count = piece_ph
                
        if current_chunk:
            final_chunks.append({"text": current_chunk, "is_cut": False})
            
    return final_chunks

# 🌟 FIX 2: Added `lang` parameter to securely pass the 'en-us' Romaji override
def synthesize_with_pauses(text: str, voice: str, speed: float, lang: str, pause_settings: Dict[str, int], behavior_settings: Dict[str, int] = None):
    if behavior_settings is None:
        behavior_settings = {}
        
    import app.state as state_module
    
    active_punc = ""
    if pause_settings.get("comma", 100) > 0: active_punc += r",，、"
    if pause_settings.get("period", 100) > 0: active_punc += r"\.。"
    if pause_settings.get("question", 100) > 0: active_punc += r"\?？"
    if pause_settings.get("exclamation", 100) > 0: active_punc += r"!！"
    if pause_settings.get("colon", 100) > 0: active_punc += r":："
    if pause_settings.get("semicolon", 100) > 0: active_punc += r";；"
    
    if active_punc:
        split_regex = f"([{active_punc}]+|\n)"
        match_regex = f"^[{active_punc}]+$"
    else:
        split_regex = r"(\n)"
        match_regex = r"^\n$"

    segments = re.split(split_regex, text)
    sample_rate = SAMPLE_RATE
    plan = []

    char_map = {
        ",": "comma", "，": "comma", "、": "comma", ".": "period", "。": "period",
        "?": "question", "？": "question", "!": "exclamation", "！": "exclamation",
        ":": "colon", "：": "colon", ";": "semicolon", "；": "semicolon",
    }

    for i, segment in enumerate(segments):
        clean_segment = segment.strip()
        
        if segment == "\n":
            dynamic_newline_ms = behavior_settings.get("N", 500)
            plan.append({"type": "silence", "ms": dynamic_newline_ms})
            continue

        if not clean_segment:
            continue

        if re.match(match_regex, clean_segment):
            last_char = clean_segment[-1]
            pause_ms = pause_settings.get(char_map.get(last_char), 100) if char_map.get(last_char) else 0
            plan.append({"type": "silence", "ms": pause_ms})
        else:
            if re.search(r"[a-zA-Z0-9\u3000-\u303f\u3040-\u309f\u30a0-\u30ff\uff00-\uff9f\u4e00-\u9faf\u3400-\u4dbf]", clean_segment):
                sub_chunks = graceful_chunk_for_tts(clean_segment)
                full_paragraph_len = estimate_phonemes(clean_segment)
                for sc_idx, sc_dict in enumerate(sub_chunks):
                    plan.append({
                        "type": "tts", 
                        "text": sc_dict["text"], 
                        "index": f"{i}_{sc_idx}",
                        "target_len": full_paragraph_len,
                        "is_cut": sc_dict["is_cut"]
                    })

    tts_tasks = [p for p in plan if p["type"] == "tts"]
    audio_map = {}

    if tts_tasks and state_module.kokoro:
        for t in tts_tasks:
            idx = t["index"]
            target_len = t.get("target_len", 510)
            try:
                # 🌟 Safely uses the passed-in `lang` override
                samples, _ = generate_locked_audio(state_module.kokoro, t["text"], voice, speed, lang, target_len)
                audio_map[idx] = samples.flatten()
            except Exception as e:
                print(f"Segment {idx} failed: {e}")
                audio_map[idx] = None

    final_segments = []
    for item in plan:
        if item["type"] == "silence":
            anti_skip_arr = create_anti_skip_silence(item["ms"], sample_rate)
            if len(anti_skip_arr) > 0:
                final_segments.append(anti_skip_arr)
        elif item["type"] == "tts":
            audio = audio_map.get(item["index"])
            if audio is not None and len(audio) > 0:
                final_segments.append(audio)

    if final_segments:
        return np.concatenate(final_segments), sample_rate
    return create_anti_skip_silence(100, sample_rate), sample_rate

def generate_cache_key(text, voice, speed, pause_settings, rules, ignore_list, behavior_settings=None, behavior_type="N"):
    lang = get_language_from_voice(voice)
    cache_data = {
        "text": text, "voice": voice, "language": lang, "speed": speed,
        "pause_settings": pause_settings,
        "rules": [str(r) for r in rules],
        "ignore_list": sorted(ignore_list),
        "behavior_settings": behavior_settings or {},
        "behavior_type": behavior_type
    }
    cache_string = json.dumps(cache_data, sort_keys=True)
    return hashlib.md5(cache_string.encode("utf-8")).hexdigest()

@router.get("/api/voices/available")
async def get_voices():
    import app.state as state_module
    if not state_module.kokoro: return {"categories": {}}
    try:
        raw_voices = state_module.kokoro.get_voices()
        categories = {}
        def get_voice_name(vid):
            parts = vid.split("_")
            return parts[1].title() if len(parts) > 1 else vid
        def get_lang_label(code):
            maps = {"en-us": "English (US)", "en-gb": "English (UK)", "fr-fr": "French", "es": "Spanish", "cmn": "Chinese (Mandarin)", "it": "Italian", "pt-br": "Portuguese (Brazil)", "ja": "Japanese"}
            return maps.get(code, "Other")

        for voice in raw_voices:
            voice_id = voice if isinstance(voice, str) else voice.get("id")
            if voice_id.lower().split("_")[-1] in ["alpha", "beta", "omega", "psi"]: continue
            
            lang_code = get_language_from_voice(voice_id)
            label = get_lang_label(lang_code)
            
            display_name = get_voice_name(voice_id)
            if voice_id == 'af_sky': display_name += ""
            elif voice_id == 'jf_nezumi': display_name += ""
            elif voice_id == 'zf_xiaoxiao': display_name += ""
            
            if lang_code not in categories: categories[lang_code] = {"label": label, "voices": []}
            categories[lang_code]["voices"].append({"id": voice_id, "name": display_name})

        for code in categories: categories[code]["voices"].sort(key=lambda x: x["name"])
        return {"categories": categories}
    except Exception: return {"categories": {}}

@router.get("/api/locale/{lang}")
async def get_locale(lang: str):
    locale_dir = base_dir / "locales"
    file_path = locale_dir / f"{lang}.json"
    if not file_path.exists(): file_path = locale_dir / "en.json"
    try:
        with open(file_path, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return {}

@router.post("/api/synthesize")
async def synthesize(request: SynthesisRequest):
    import app.state as state_module
    from fastapi import HTTPException
    
    if state_module.kokoro is None: raise HTTPException(status_code=503, detail="TTS Engine not initialized.")

    original_text = request.text
    structural_pause_ms = 0
    
    pause_match = re.search(r"\[PAUSE_(\d+)\]\s*", original_text)
    if pause_match:
        structural_pause_ms = int(pause_match.group(1))
        original_text = original_text.replace(pause_match.group(0), "")

    # FRONTEND TAG VAPORIZER
    safe_text = re.sub(r'<img[^>]*>', '', original_text, flags=re.IGNORECASE)
    safe_text = re.sub(r'<[^>]+>', '', safe_text) 
    safe_text = re.sub(r'\[(?:IMAGE|IMG|VIDEO).*?\]', '', safe_text, flags=re.IGNORECASE)
    
    request.text = safe_text
    original_text = safe_text 

    # 1. Secure Voice and Language First
    try:
        voices = state_module.kokoro.get_voices()
        selected_voice = request.voice if request.voice in voices else "af_heart"
        main_voice_lang = get_language_from_voice(selected_voice)
    except Exception:
        selected_voice = "af_heart"
        main_voice_lang = "en-us"

    # 2. Process Text Normalization with Active Language Context
    try:
        text = fix_special_formats(request.text, main_voice_lang)
        rules_data = [r.model_dump() for r in request.rules] if request.rules else []
        text = apply_custom_pronunciations(text, rules_data, request.ignore_list, main_voice_lang)
    except Exception:
        text = fix_special_formats(request.text, main_voice_lang)

    try:
        # POLYGLOT SLICER INTERCEPTOR
        polyglot_segments = smart_polyglot_split(text, selected_voice, get_language_from_voice)
        
        pause_settings = request.pause_settings or {}
        b_type = request.behavior_type or "N"
        b_settings = request.behavior_settings or {"H": 2000, "Img": 3000, "S": 1000, "N": 500}
        
        if b_type.startswith("H"):
            h_base = b_settings.get("H", 2000)
            if h_base <= 0: behavior_pause_ms = 0
            elif b_type == "H1": behavior_pause_ms = h_base
            elif b_type == "H2": behavior_pause_ms = h_base / 2.0
            elif b_type == "H3": behavior_pause_ms = (h_base / 2.0) / 1.5
            else: behavior_pause_ms = ((h_base / 2.0) / 1.5) / 1.5
            behavior_pause_ms = int(behavior_pause_ms)
        else:
            behavior_pause_ms = b_settings.get(b_type, 500)

        cache_key = generate_cache_key(original_text, selected_voice, float(request.speed or 1.0), pause_settings, request.rules, request.ignore_list, b_settings, b_type)
        
        cached_audio = audio_cache.get(cache_key)
        if cached_audio:
            return StreamingResponse(io.BytesIO(cached_audio), media_type="audio/wav", headers={"Content-Length": str(len(cached_audio))})

        has_pause_settings = any(val > 0 for val in pause_settings.values()) if isinstance(pause_settings, dict) else False
        punctuation_chars = [",", ".", "!", "?", ":", ";", "\n", "。", "，", "！", "？", "：", "；", "、"]
        has_punctuation = any(p in text for p in punctuation_chars)

        if not re.search(r"[a-zA-Z0-9\u3000-\u303f\u3040-\u309f\u30a0-\u30ff\uff00-\uff9f\u4e00-\u9faf\u3400-\u4dbf]", text):
            total_pause = structural_pause_ms + behavior_pause_ms
            if total_pause <= 0: total_pause = 100
            samples = create_anti_skip_silence(total_pause, 24000)
            sample_rate = 24000
        else:
            chunk_audios = []
            sample_rate = 24000
            
            main_voice_lang = get_language_from_voice(selected_voice)
            
            for seg in polyglot_segments:
                raw_text = seg['text']
                seg_voice = seg['voice']
                detected_lang = seg['lang']
                native_voice_lang = get_language_from_voice(seg_voice)
                
                if not raw_text.strip(): 
                    continue
                    
                # ==========================================
                # THE POLYGLOT RESOLUTION MATRIX (Overlap Protection)
                # ==========================================
                final_text = raw_text
                final_engine_lang = native_voice_lang
                
                # 1. JAPANESE OVERLAP PROTECTION (Pure-Python Bypass)
                if detected_lang == 'ja' or native_voice_lang.startswith('ja'):
                    final_text = pure_japanese_to_romaji(final_text)
                    # Force engine to read Romaji via US phonetics to bypass missing C++ openjtalk dependency
                    final_engine_lang = 'en-us'
                
                # 2. CHINESE OVERLAP PROTECTION (Acoustic Shield)
                elif detected_lang == 'cmn' or native_voice_lang.startswith('cmn') or native_voice_lang.startswith('zh'):
                    final_text = cleanse_chinese_text(final_text)
                    # If Main Voice is English but reading Chinese Pinyin, protect the English phonetic rules
                    if main_voice_lang.startswith('en') and seg_voice == selected_voice:
                        final_engine_lang = 'en-us'
                    else:
                        final_engine_lang = native_voice_lang
                
                # 3. ENGLISH & EUROPEAN OVERLAP PROTECTION
                else:
                    # If a Main Japanese/Chinese Voice encounters isolated English text, 
                    # switch engine flag to 'en-us' to prevent tokenizer crash and silence
                    if main_voice_lang.startswith('ja') or main_voice_lang.startswith('cmn'):
                        final_engine_lang = 'en-us'
                    else:
                        final_engine_lang = native_voice_lang

                # Re-verify text is not empty after conversion filters to prevent zero-length crashes
                if not final_text.strip():
                    continue

                # ==========================================
                # AUDIO GENERATION & QUEUE RECORDING
                # ==========================================
                try:
                    if has_pause_settings and has_punctuation:
                        seg_samples, sr = synthesize_with_pauses(
                            text=final_text, 
                            voice=seg_voice, 
                            speed=float(request.speed or 1.0), 
                            lang=final_engine_lang, 
                            pause_settings=pause_settings, 
                            behavior_settings=b_settings
                        )
                        if seg_samples is not None and len(seg_samples.flatten()) > 0: 
                            chunk_audios.append(seg_samples.flatten())
                        sample_rate = sr
                    else:
                        sub_chunks = graceful_chunk_for_tts(final_text)
                        full_paragraph_len = estimate_phonemes(final_text)
                        for chunk_dict in sub_chunks:
                            chunk_samples, sr = generate_locked_audio(
                                kokoro_inst=state_module.kokoro, 
                                text=chunk_dict["text"], 
                                voice=seg_voice, 
                                speed=float(request.speed or 1.0), 
                                lang=final_engine_lang, 
                                target_len=full_paragraph_len
                            )
                            if chunk_samples is not None and len(chunk_samples.flatten()) > 0: 
                                chunk_audios.append(chunk_samples.flatten())
                            sample_rate = sr
                            
                except Exception as loop_e:
                    print(f"[Engine] Segment skip protection triggered for '{final_text[:10]}': {loop_e}")
                    continue
                        
            samples = np.concatenate(chunk_audios) if chunk_audios else np.array([], dtype=np.float32)

            if b_type == "N" and text.endswith("\n") and has_pause_settings and has_punctuation:
                behavior_pause_ms = 0
                
            total_end_pause_ms = structural_pause_ms + behavior_pause_ms
            
            if total_end_pause_ms > 0:
                frames = int((total_end_pause_ms / 1000.0) * sample_rate)
                pause_arr = np.random.uniform(-1e-4, 1e-4, frames).astype(np.float32)
                if len(samples) > 0:
                    samples = np.concatenate([samples, pause_arr])
                else:
                    samples = pause_arr

            if len(samples) == 0:
                samples = create_anti_skip_silence(100, 24000)

        buffer = io.BytesIO()
        sf.write(buffer, samples.flatten(), sample_rate, format="WAV", subtype="FLOAT")
        audio_bytes = buffer.getvalue()
        
        audio_cache.put(cache_key, audio_bytes)

        return StreamingResponse(io.BytesIO(audio_bytes), media_type="audio/wav", headers={"Content-Length": str(len(audio_bytes))})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))