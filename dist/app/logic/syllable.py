import re

# --- THE NUMBER ENGINE ---
try:
    from num2words import num2words
    HAS_NUM2WORDS = True
except ImportError:
    HAS_NUM2WORDS = False

# --- THE PHONEME ENGINE ---
try:
    from phonemizer import phonemize
    from phonemizer.backend.espeak.wrapper import EspeakWrapper
    import espeakng_loader
    
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
    text = text.strip()
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
        
    except Exception as e:
        print(f"[Chunker] Critical phoneme estimation failure. Bypassing with fallback: {e}")
        # Extreme fallback if espeak crashes on corrupted characters
        return int(len(text) * 1.25)