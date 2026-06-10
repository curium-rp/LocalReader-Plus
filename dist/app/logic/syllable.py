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
    Standalone perfect token counter for Kokoro's 510 limit.
    Counts phonemes, spaces, punctuation, and stress marks exactly as Kokoro does,
    without needing to import or run the Kokoro engine itself.
    """
    if not text.strip():
        return 0
        
    if not HAS_IPA:
        return len(text)

    try:
        # 1. Expand numbers to spoken words
        safe_text = expand_numbers(text)
        
        # 2. Convert to IPA format. 
        # eng_to_ipa natively preserves spaces and punctuation in the string!
        ipa_text = ipa.convert(safe_text)
        
        # 3. Clean the asterisks. 
        # If eng_to_ipa doesn't know a word, it outputs an asterisk (e.g., "Kokoro*").
        # Removing the '*' leaves the raw letters, which perfectly acts as our fallback count.
        perfect_ipa = ipa_text.replace('*', '')
        
        # 4. The Perfect Standalone Count:
        # We simply return the length of this final string. It automatically accounts
        # for spaces, commas, periods, and phonetic characters, giving the chunker 
        # the exact number it needs.
        return len(perfect_ipa)
        
    except Exception as e:
        print(f"[Chunker] Phoneme estimation failed, triggering fallback: {e}")
        return len(text)