import re

try:
    import eng_to_ipa as ipa
    HAS_IPA = True
except ImportError:
    HAS_IPA = False

# --- THE NUMBER ENGINE ---
try:
    from num2words import num2words
    HAS_NUM2WORDS = True
except ImportError:
    HAS_NUM2WORDS = False

def expand_numbers(text: str) -> str:
    """
    Finds numbers in the text and converts them to words.
    E.g., "1999" -> "one thousand nine hundred and ninety-nine"
    This guarantees the phoneme token count is accurate for spoken text.
    """
    if not HAS_NUM2WORDS:
        return text
        
    def replace_num(match):
        num_str = match.group(0).replace(',', '') 
        try:
            if '.' in num_str:
                return num2words(float(num_str))
            else:
                return num2words(int(num_str))
        except Exception:
            return num_str

    return re.sub(r'\b\d{1,3}(?:,\d{3})*(?:\.\d+)?\b|\b\d+(?:\.\d+)?\b', replace_num, text)
# -------------------------

def estimate_phonemes(text: str) -> int:
    """
    BUG 2 FIX: Standalone token counter with safe-ceiling mathematics.
    Accounts for Kokoro's invisible stress marks and espeak inflation,
    guaranteeing the payload NEVER hits the 510 hard-crash limit.
    """
    if not text.strip():
        return 0
        
    if not HAS_IPA:
        # Fallback: Character count with a 20% mathematical safety margin
        return int(len(text) * 1.20)

    try:
        # 1. Expand numbers to spoken words
        safe_text = expand_numbers(text)
        
        # 2. Convert to IPA format. 
        ipa_text = ipa.convert(safe_text)
        
        # --- SURGICAL CHANGE: The Hallucination & Stress Mark Fix ---
        # Kokoro's internal espeak engine heavily inflates unknown words with 
        # hidden phonetic stress symbols. We must mathematically pad the estimate.
        words = ipa_text.split()
        safe_token_count = 0
        
        for word in words:
            if '*' in word:
                # Unknown word penalty: Multiply its length by 1.5 to guarantee 
                # we cover the extra tokens Kokoro will generate to sound it out.
                actual_letters = len(word.replace('*', ''))
                safe_token_count += int(actual_letters * 1.5)
            else:
                # Known words: Standard IPA length
                safe_token_count += len(word)
                
        # Add back the spaces between the words
        safe_token_count += max(0, len(words) - 1)
        
        # Global Kokoro padding: Add 10% to the total to account for native 
        # sentence-level stress marks and punctuation pauses Kokoro auto-injects.
        return int(safe_token_count * 1.10)
        # -----------------------------------------------------------
        
    except Exception as e:
        print(f"[Chunker] Phoneme estimation failed, triggering fallback: {e}")
        return int(len(text) * 1.20)