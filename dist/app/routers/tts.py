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
import threading
import multiprocessing

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
    from japanese_g2p import pure_japanese_to_romaji
    from chinese_g2p import cleanse_chinese_text 

from ..state import audio_cache, kokoro
from ..models import SynthesisRequest
from ..utils import get_language_from_voice
from ..config import base_dir
from kokoro_onnx import SAMPLE_RATE

router = APIRouter()

def create_anti_skip_silence(duration_ms, sample_rate=24000):
    if duration_ms <= 0:
        return np.array([], dtype=np.float32)
    frames = int((duration_ms / 1000.0) * sample_rate)
    return np.random.uniform(-1e-4, 1e-4, frames).astype(np.float32)

_total_cores = multiprocessing.cpu_count()
_reserved_cores = max(1, _total_cores // 6)
_safe_cores = max(1, _total_cores - _reserved_cores)
cpu_semaphore = threading.Semaphore(_safe_cores)

_robust_link_cache = {}
_cache_lock = threading.Lock()

def generate_locked_audio(kokoro_inst, text, voice, speed, lang, target_len):
    cache_string = f"{text}|{voice}|{speed}|{lang}".encode('utf-8')
    cache_key = hashlib.md5(cache_string).hexdigest()
    
    with _cache_lock:
        if cache_key in _robust_link_cache:
            return _robust_link_cache[cache_key]
            
    try:
        with cpu_semaphore:
            audio_data = kokoro_inst.create(text, voice, speed, lang)
            
        if audio_data is not None and len(audio_data[0].flatten()) > 0:
            with _cache_lock:
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

# ==========================================
# 🌟 NEW FUNCTION: PRE-PROCESSOR SHIELD 🌟
# ==========================================
def sanitize_typography_for_engine(text: str) -> str:
    if not text:
        return text
        
    import re
    import unicodedata

    # ==========================================
    # 🛡️ PHASE 1: UNICODE & INVISIBLE CHARACTERS
    # ==========================================
    # 1. NFKC Normalization: Converts "Full-Width" Asian/English hybrids (Ｈｅｌｌｏ) 
    # and weird ligatures into standard ASCII so Kokoro can read them.
    text = unicodedata.normalize('NFKC', text)

    # 2. Vaporize Invisible Formatting (Zero-Width Spaces, BOMs, LRM/RLM markers)
    # These characters are invisible but cause Kokoro to output heavy, awkward pauses.
    text = re.sub(r'[\u200B-\u200D\uFEFF\u200E\u200F\u00A0]', ' ', text)

    # 3. The Emoji & Symbol Vaporizer 
    # Kokoro crashes on Emojis. This regex safely strips out high-plane emojis 
    # while perfectly preserving English, Japanese, Chinese, and standard punctuation.
    text = re.sub(r'[\U00010000-\U0010ffff]', '', text)

    # ==========================================
    # 🛡️ PHASE 2: TYPOGRAPHY & PUNCTUATION
    # ==========================================
    # 4. Standardize all quote formats
    text = text.replace('’', "'").replace('‘', "'").replace('“', '"').replace('”', '"')
    text = text.replace('«', '"').replace('»', '"').replace('`', "'")
    
    # 5. Asian punctuation to Standard ASCII
    text = text.replace('。', '.').replace('、', ',').replace('！', '!').replace('？', '?')
    text = text.replace('…', '...').replace('．', '.').replace('，', ',')
    
    # 6. Normalize mutant ellipses (e.g., ".....", "....") into a standard 3 dots
    text = re.sub(r'\.{4,}', '...', text)

    # ==========================================
    # 🛡️ PHASE 3: THE COMMA & NUMBER SHIELDS
    # ==========================================
    # 7. Protect decimals and thousands separators (100,000 / 3.14)
    text = re.sub(r'(?<=\d),(?=\d)', '<NUM_COM>', text)
    text = re.sub(r'(?<=\d)\.(?=\d)', '<NUM_DOT>', text)
    text = re.sub(r'(?<=\d):(?=\d)', '<NUM_COL>', text)
    
    # 8. Group 1: Zero-Pause Bypass (Direct addresses, conversational glue, tags)
    g1_before = r"(?i),\s*(too|sir|ma'am|yeah|then|ever|right|isn't it|is it|do you|don't you|won't you|can you|will you|man|bro|buddy|honey|darling|my lord|child|boy|girl|guys|idiot|fool|no matter what)(?:\b|\?)"
    text = re.sub(g1_before, r'<BYP_COM> \1', text)

    g1_after = r"(?i)\b(oh|ah|me|you|wait|please|no|yes)\s*,"
    text = re.sub(g1_after, r'\1<BYP_COM>', text)

    # 9. Group 2: Micro-Pause Cap (Transitions, starters, modifiers)
    g2_after = r"(?i)\b(again|then|huh|after all|are they|guys|anyway|either|indeed|though|now|later|soon|first|next|finally|suddenly|however|therefore|furthermore|in fact|of course|for example|meanwhile|otherwise|besides|honestly|seriously|luckily|fortunately|sadly|obviously|clearly|actually|basically|well|so|say|listen|look)\s*,"
    text = re.sub(g2_after, r'\1<CAP_COM>', text)
    
    # 10. Fix squished punctuation safely (e.g., "Wait!Stop" -> "Wait! Stop")
    text = re.sub(r'([\.!\?:;,]+)(?=[a-zA-Z0-9])', r'\1 ', text)
    
    # 11. Drop the Number shield (So normalizers can read the numbers correctly)
    text = text.replace('<NUM_COM>', ',').replace('<NUM_DOT>', '.').replace('<NUM_COL>', ':')
    
    # 12. Final cleanup of multiple spaces created by replacements
    text = re.sub(r'\s+', ' ', text)
    
    return text.strip()

def synthesize_with_pauses(text: str, voice: str, speed: float, lang: str, pause_settings: Dict[str, int], behavior_settings: Dict[str, int] = None):
    if behavior_settings is None:
        behavior_settings = {}
        
    import app.state as state_module
    import re
    
    # 🌟 SHIELD: Strip trailing newlines to prevent the internal loop from 
    # double-stacking 500ms on top of your H1/Img behavioral pauses!
    text = text.strip()
    
    
    # Number shield (Phase 2)
    text = re.sub(r'(?<=\d),(?=\d)', '<NUM_COM>', text)
    text = re.sub(r'(?<=\d)\.(?=\d)', '<NUM_DOT>', text)
    text = re.sub(r'(?<=\d):(?=\d)', '<NUM_COL>', text)
    
    # 🌟 ADDED <CAP_COM> TO THE SPLITTER 🌟
    # Notice <BYP_COM> is missing. This intentionally traps Group 1 commas 
    # in the buffer so they are bypassed completely!
    split_regex = r"([,，、\.。\?？!！:：;；]+|<CAP_COM>|\n)"
    raw_pieces = re.split(split_regex, text)
    
    sample_rate = SAMPLE_RATE
    plan = []
    char_map = {",": "comma", "，": "comma", "、": "comma", ".": "period", "。": "period", "?": "question", "？": "question", "!": "exclamation", "！": "exclamation", ":": "colon", "：": "colon", ";": "semicolon", "；": "semicolon"}
    
    current_text_buffer = ""
    
    for i in range(0, len(raw_pieces), 2):
        text_chunk = raw_pieces[i]
        punc_chunk = raw_pieces[i+1] if i + 1 < len(raw_pieces) else ""
        
        current_text_buffer += text_chunk + punc_chunk
        punc_str = punc_chunk.strip()
        
        pause_ms = 0
        
        if punc_chunk == "\n":
            pause_ms = behavior_settings.get("N", 500)
        elif punc_str == "<CAP_COM>":
            # ==========================================
            # 🌟 ROUTE C: DYNAMIC MICRO-PAUSE CAP 🌟
            # ==========================================
            base_pause = int(pause_settings.get("comma", 0))
            dynamic_cap = int(150 / speed) # Math: 1.0x = 150ms | 1.35x = 111ms
            
            # Use the user's comma setting, UNLESS it exceeds the dynamic cap.
            pause_ms = min(base_pause, dynamic_cap) if base_pause > 0 else 0
            
        elif punc_str:
            spam_count = len(re.findall(r'[\.。\?？!！]', punc_str))
            
            if spam_count >= 2:
                spam_multiplier = int(pause_settings.get("period", 0))
                pause_ms = spam_multiplier * (spam_count - 1)
            else:
                last_char = punc_str[-1]
                mapped_key = char_map.get(last_char)
                if mapped_key:
                    pause_ms = int(pause_settings.get(mapped_key, 0))
                    
        if pause_ms > 0:
            has_words = bool(re.search(r'[a-zA-Z0-9\u3040-\u30ff\u4e00-\u9faf]', current_text_buffer))
            
            if has_words:
                # 🌟 UNSHIELD EVERYTHING 🌟
                clean_text = current_text_buffer.strip().replace('<NUM_COM>', ',').replace('<NUM_DOT>', '.').replace('<NUM_COL>', ':').replace('<BYP_COM>', ',').replace('<CAP_COM>', ',')
                
                plan.append({"type": "tts", "text": clean_text})
                plan.append({"type": "silence", "ms": pause_ms})
                current_text_buffer = "" 
            else:
                plan.append({"type": "silence", "ms": pause_ms})

    if current_text_buffer.strip():
        has_words = bool(re.search(r'[a-zA-Z0-9\u3040-\u30ff\u4e00-\u9faf]', current_text_buffer))
        if has_words:
            # 🌟 UNSHIELD EVERYTHING ON FINAL FLUSH 🌟
            clean_text = current_text_buffer.strip().replace('<NUM_COM>', ',').replace('<NUM_DOT>', '.').replace('<NUM_COL>', ':').replace('<BYP_COM>', ',').replace('<CAP_COM>', ',')
            plan.append({"type": "tts", "text": clean_text})

    # ==========================================
    # AUDIO GENERATION & CONCATENATION 
    # ==========================================
    audio_map = {}
    tts_tasks = [p for p in plan if p["type"] == "tts"]

    if tts_tasks and state_module.kokoro:
        for idx, t in enumerate(tts_tasks):
            t["index"] = f"task_{idx}" 
            full_len = estimate_phonemes(t["text"])
            
            sub_chunks = graceful_chunk_for_tts(t["text"])
            chunk_audios = []
            
            for sc_dict in sub_chunks:
                try:
                    samples, _ = generate_locked_audio(state_module.kokoro, sc_dict["text"], voice, speed, lang, full_len)
                    if samples is not None and len(samples.flatten()) > 0:
                        chunk_audios.append(samples.flatten())
                except Exception as e:
                    print(f"Segment failed: {e}")
                    
            audio_map[t["index"]] = np.concatenate(chunk_audios) if chunk_audios else None

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
    import re
    import io
    import numpy as np
    import soundfile as sf
    from fastapi.responses import StreamingResponse
    
    if state_module.kokoro is None: raise HTTPException(status_code=503, detail="TTS Engine not initialized.")

    original_text = request.text
    
    # 🌟 CAPTURE TRAILING NEWLINE BEFORE SANITIZER EATS IT
    had_trailing_newline = original_text.endswith('\n')
    
    structural_pause_ms = 0
    pause_match = re.search(r"\[PAUSE_(\d+)\]\s*", original_text)
    if pause_match:
        structural_pause_ms = int(pause_match.group(1))
        original_text = original_text.replace(pause_match.group(0), "")

    safe_text = re.sub(r'<img[^>]*>', '', original_text, flags=re.IGNORECASE)
    safe_text = re.sub(r'<[^>]+>', '', safe_text) 
    safe_text = re.sub(r'\[(?:IMAGE|IMG|VIDEO).*?\]', '', safe_text, flags=re.IGNORECASE)
    
    safe_text = sanitize_typography_for_engine(safe_text)
    
    request.text = safe_text
    original_text = safe_text 

    try:
        voices = state_module.kokoro.get_voices()
        selected_voice = request.voice if request.voice in voices else "af_heart"
        main_voice_lang = get_language_from_voice(selected_voice)
    except Exception:
        selected_voice = "af_heart"
        main_voice_lang = "en-us"

    try:
        text = fix_special_formats(request.text, main_voice_lang)
        rules_data = [r.model_dump() for r in request.rules] if request.rules else []
        text = apply_custom_pronunciations(text, rules_data, request.ignore_list, main_voice_lang)
    except Exception:
        text = fix_special_formats(request.text, main_voice_lang)

    try:
        polyglot_segments = smart_polyglot_split(text, selected_voice, get_language_from_voice)
        
        pause_settings = request.pause_settings or {}
        b_type = request.behavior_type or "N"
        b_settings = request.behavior_settings or {"H": 2000, "Img": 3000, "S": 1000, "N": 500}
        
        # ==========================================
        # 🌟 NEW: THE SPLIT-PAUSE BEHAVIOR ENGINE 🌟
        # ==========================================
        behavior_front_pause_ms = 0
        behavior_end_pause_ms = 0
        
        if b_type.startswith("H"):
            h_base = b_settings.get("H", 2000)
            if h_base > 0:
                if b_type == "H1": base_calc = h_base
                elif b_type == "H2": base_calc = h_base / 2.0
                elif b_type == "H3": base_calc = (h_base / 2.0) / 1.5
                else: base_calc = ((h_base / 2.0) / 1.5) / 1.5
                
                # The Golden Ratio for Headings!
                # 100% pause BEFORE reading, 30% pause AFTER reading. Max cap 10 seconds.
                behavior_front_pause_ms = min(int(base_calc), 10000) 
                behavior_end_pause_ms = min(int(base_calc * 0.30), 10000)
                
        elif b_type == "Img":
            behavior_end_pause_ms = int(b_settings.get("Img", 3000))
            
        elif b_type == "S":
            behavior_end_pause_ms = int(b_settings.get("S", 1000))
            
        elif b_type == "N":
            if had_trailing_newline:
                stripped_text = original_text.strip()
                has_hard_punc_at_end = False
                
                if stripped_text:
                    last_char = stripped_text[-1]
                    if last_char in [".", "!", "?", "。", "！", "？", "…"]:
                        has_hard_punc_at_end = True
                    elif len(stripped_text) > 1 and last_char in ['"', "'", '”', '’', '」', '』']:
                        if stripped_text[-2] in [".", "!", "?", "。", "！", "？", "…"]:
                            has_hard_punc_at_end = True
                
                # THE NULLIFIER
                if has_hard_punc_at_end:
                    behavior_end_pause_ms = 0
                else:
                    behavior_end_pause_ms = int(b_settings.get("N", 500))
            if had_trailing_newline:
                stripped_text = original_text.strip()
                has_hard_punc_at_end = False
                
                if stripped_text:
                    last_char = stripped_text[-1]
                    # Check 1: Does it end exactly on a punctuation mark?
                    if last_char in [".", "!", "?", "。", "！", "？", "…"]:
                        has_hard_punc_at_end = True
                    # Check 2: The Quote Penetrator (e.g. "Are you crazy?!")
                    elif len(stripped_text) > 1 and last_char in ['"', "'", '”', '’', '」', '』']:
                        if stripped_text[-2] in [".", "!", "?", "。", "！", "？", "…"]:
                            has_hard_punc_at_end = True
                
                # 🌟 THE NULLIFIER: If punctuation handled the pause, N becomes 0!
                if has_hard_punc_at_end:
                    behavior_pause_ms = 0
                else:
                    behavior_pause_ms = int(b_settings.get("N", 500))

        cache_key = generate_cache_key(original_text, selected_voice, float(request.speed or 1.0), pause_settings, request.rules, request.ignore_list, b_settings, b_type)
        
        cached_audio = audio_cache.get(cache_key)
        if cached_audio:
            return StreamingResponse(io.BytesIO(cached_audio), media_type="audio/wav", headers={"Content-Length": str(len(cached_audio))})

        punctuation_chars = [",", ".", "!", "?", ":", ";", "\n", "。", "，", "！", "？", "：", "；", "、"]
        has_punctuation = any(p in text for p in punctuation_chars)

        if not re.search(r"[a-zA-Z0-9\u3000-\u303f\u3040-\u309f\u30a0-\u30ff\uff00-\uff9f\u4e00-\u9faf\u3400-\u4dbf]", text):
            total_pause = structural_pause_ms + behavior_end_pause_ms + behavior_front_pause_ms
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
                    
                final_text = raw_text
                final_engine_lang = native_voice_lang
                
                if detected_lang == 'ja' or native_voice_lang.startswith('ja'):
                    final_text = pure_japanese_to_romaji(final_text)
                    final_engine_lang = 'en-us'
                elif detected_lang == 'cmn' or native_voice_lang.startswith('cmn') or native_voice_lang.startswith('zh'):
                    final_text = cleanse_chinese_text(final_text)
                    if main_voice_lang.startswith('en') and seg_voice == selected_voice:
                        final_engine_lang = 'en-us'
                    else:
                        final_engine_lang = native_voice_lang
                else:
                    if main_voice_lang.startswith('ja') or main_voice_lang.startswith('cmn'):
                        final_engine_lang = 'en-us'
                    else:
                        final_engine_lang = native_voice_lang

                if not final_text.strip():
                    continue

                try:
                    if has_punctuation:
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

            total_front_pause_ms = behavior_front_pause_ms
            total_end_pause_ms = structural_pause_ms + behavior_end_pause_ms
            
            # 🌟 Apply the Pre-Reading Delay (Front Pause)
            if total_front_pause_ms > 0:
                frames = int((total_front_pause_ms / 1000.0) * sample_rate)
                front_pause_arr = np.random.uniform(-1e-4, 1e-4, frames).astype(np.float32)
                if len(samples) > 0:
                    samples = np.concatenate([front_pause_arr, samples])
                else:
                    samples = front_pause_arr

            # 🌟 Apply the Post-Reading Delay (End Pause)
            if total_end_pause_ms > 0:
                frames = int((total_end_pause_ms / 1000.0) * sample_rate)
                end_pause_arr = np.random.uniform(-1e-4, 1e-4, frames).astype(np.float32)
                if len(samples) > 0:
                    samples = np.concatenate([samples, end_pause_arr])
                else:
                    samples = end_pause_arr

            if len(samples) == 0:
                samples = create_anti_skip_silence(100, 24000)

        buffer = io.BytesIO()
        sf.write(buffer, samples.flatten(), sample_rate, format="WAV", subtype="FLOAT")
        audio_bytes = buffer.getvalue()
        
        audio_cache.put(cache_key, audio_bytes)

        return StreamingResponse(io.BytesIO(audio_bytes), media_type="audio/wav", headers={"Content-Length": str(len(audio_bytes))})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))