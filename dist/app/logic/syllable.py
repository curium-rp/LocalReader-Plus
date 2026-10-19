import re
import threading

# All callers import app.logic.syllable so eSpeak shares one lock.
# Audio inference stays outside this lock.
phonemizer_lock = threading.RLock()


def phonemize(*args, **kwargs):
    with phonemizer_lock:
        return _phonemize(*args, **kwargs)

# --- THE NUMBER ENGINE ---
try:
    from num2words import num2words
    HAS_NUM2WORDS = True
except ImportError:
    HAS_NUM2WORDS = False

# --- THE PHONEME ENGINE ---
try:
    from phonemizer import phonemize as _phonemize
    from phonemizer.backend.espeak.wrapper import EspeakWrapper
    import espeakng_loader
    
    with phonemizer_lock:
        EspeakWrapper.set_library(espeakng_loader.get_library_path())
        EspeakWrapper.set_data_path(espeakng_loader.get_data_path())
    
    HAS_PHONEMIZER = True
except ImportError:
    HAS_PHONEMIZER = False

def expand_numbers(text: str) -> str:
    """
    Converts numerical digits into spoken words to match TTS audio generation.
    E.g., "1999" -> "one thousand nine hundred and ninety-nine"
    """
    if not HAS_NUM2WORDS:
        return text
        
    def replace_num(match):
        num_str = match.group(0).replace(',', '') 
        try:
            if '.' in num_str:
                return num2words(float(num_str))
            return num2words(int(num_str))
        except Exception:
            return num_str

    # Matches standard numbers, comma-separated thousands, and decimals.
    return re.sub(r'\b\d{1,3}(?:,\d{3})*(?:\.\d+)?\b|\b\d+(?:\.\d+)?\b', replace_num, text)

def estimate_phonemes(text: str) -> int:
    """
    Calculates the exact token footprint for Kokoro-ONNX to prevent 510-token crashes.
    Uses espeak-ng via phonemizer to mirror Kokoro's internal G2P engine perfectly.
    """
    if '<' in text:
        # 1. Skip/Destroy <img> and <s> (scene break) tags completely
        text = re.sub(r'<img[^>]*>', '', text, flags=re.IGNORECASE)
        text = re.sub(r'<s\b[^>]*>.*?</s>', '', text, flags=re.IGNORECASE|re.DOTALL)
        text = re.sub(r'<s[^>]*>', '', text, flags=re.IGNORECASE)
        
        text = re.sub(r'<[^>]+>', ' ', text)
        
    # 3. The Line Mismatch Shield (Fixes input/output crashes by replacing newlines)
    text = text.replace('\n', ' ')
    text = re.sub(r'\s+', ' ', text).strip()
    
    if not text:
        return 0
        
    if not HAS_PHONEMIZER:
        # Fallback: Character count with a strict 25% safety margin if libraries are missing.
        return int(len(text) * 1.25)

    try:
        # 1. Expand numbers to spoken words for accurate phonetic length
        spoken_text = expand_numbers(text)
        
        # 2. Generate exact IPA string using Kokoro's native backend (espeak)
        ipa_text = phonemize(
            spoken_text,
            language='en-us',
            backend='espeak',
            strip=True,
            preserve_punctuation=True,
            with_stress=True
        )
        
        # 3. Mathematical Token Calculation
        # Kokoro maps nearly all individual IPA characters to single tensor tokens.
        base_tokens = len(ipa_text)
        
        # 4. Engine Overhead & Padding
        # - Kokoro requires 2 hidden boundary tokens (Start/End).
        # - We apply a tight 5% padding to account for rare multi-char token combinations 
        #   or punctuation spacing discrepancies in the ONNX graph.
        boundary_tokens = 2
        safety_multiplier = 1.05 
        
        final_estimate = int((base_tokens + boundary_tokens) * safety_multiplier)
        
        return final_estimate
        
    except Exception:
        # Silent extreme fallback if espeak crashes on corrupted characters
        return int(len(text) * 1.25)


def graceful_chunk_for_tts(text, soft_limit=490, hard_limit=510):
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
